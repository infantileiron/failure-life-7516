import threading

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.services import dataset, run
from src.services.seed import seed
from src.utils import repository as repo


@pytest.fixture(scope="session", autouse=True)
def prepared():
    repo.init_db()
    seed()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def token(client):
    r = client.post("/auth/login", json={"email": "analyst@lichuan.test", "password": "Life2026!"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture()
def fresh_dataset():
    rows = [
        {"specimen_id": "S-1", "group": "A", "duration_hours": 10.0, "event": 1, "censored": 0},
        {"specimen_id": "S-2", "group": "A", "duration_hours": 20.0, "event": 0, "censored": 1},
        {"specimen_id": "S-3", "group": "B", "duration_hours": 30.0, "event": 1, "censored": 0},
        {"specimen_id": "S-4", "group": "B", "duration_hours": 40.0, "event": 0, "censored": 1},
    ]
    from src.models.entities import SpecimenDataset
    from src.services.dataset import _create_with_version
    from src.utils.identity import now
    item = SpecimenDataset("ds-test-" + __import__("uuid").uuid4().hex[:8], "t", "s", rows, now())
    from dataclasses import asdict
    _create_with_version(asdict(item))
    return item.id


def _batch(csv_text, revision, source="b.csv", note=""):
    return type("Batch", (), {"csv_text": csv_text, "revision": revision, "source": source, "note": note})


def _withdraw(ids, revision, source="w.csv", note=""):
    return type("Batch", (), {"specimen_ids": ids, "revision": revision, "source": source, "note": note})


def test_increment_merge_and_same_content_ignore(fresh_dataset):
    ds = fresh_dataset
    csv = "specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-2,A,25,0\nS-9,B,50,1\n"
    result = dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    assert result["status"] == "applied"
    assert result["counts"] == {"written": 2, "ignored": 1, "withdrawn": 0}
    reasons = {d["specimen_id"]: d["reason"] for d in result["details"]}
    assert reasons == {"S-1": "unchanged", "S-2": "corrected", "S-9": "new"}
    version = dataset.get_version(ds, 2)
    assert len(version["rows"]) == 5
    assert version["specimen_revisions"]["S-2"] == 1
    assert version["specimen_revisions"]["S-1"] == 0


def test_replay_same_revision_same_content_is_ignored_and_not_double_counted(fresh_dataset):
    ds = fresh_dataset
    csv = "specimen_id,group,duration_hours,event\nS-1,A,11,1\n"
    first = dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    before_versions = sum(1 for v in repo.all_rows("dataset_versions") if v["dataset_id"] == ds)
    before_count = repo.get("datasets", ds)["version_count"]
    replay = dataset.apply_specimens(ds, _batch(csv, 1, note="重试"), "analyst@lichuan.test")
    assert replay["status"] == "replayed" and replay["replay"]
    assert replay["counts"] == {"written": 0, "ignored": 1, "withdrawn": 0}
    assert replay["original_counts"] == first["original_counts"]
    assert replay["replay_count"] == 1
    assert sum(1 for v in repo.all_rows("dataset_versions") if v["dataset_id"] == ds) == before_versions
    dataset_now = repo.get("datasets", ds)
    assert dataset_now["latest_revision"] == 1
    assert dataset_now["version_count"] == before_count
    # 再次重放不会重复累计
    again = dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    assert again["replay_count"] == 2
    assert sum(1 for v in repo.all_rows("dataset_versions") if v["dataset_id"] == ds) == before_versions


def test_same_revision_different_content_rejects_whole_batch(fresh_dataset):
    ds = fresh_dataset
    dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,12,1\n", 1), "analyst@lichuan.test")
    before = repo.get("datasets", ds)
    with pytest.raises(Exception) as exc:
        dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,13,1\n", 1), "analyst@lichuan.test")
    assert getattr(exc.value, "status_code", None) == 409
    after = repo.get("datasets", ds)
    assert after["latest_version_id"] == before["latest_version_id"]
    assert after["version_count"] == before["version_count"]


def test_withdrawal_keeps_version_and_blocks_stale_resurrection(fresh_dataset):
    ds = fresh_dataset
    dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,10,1\n", 1), "analyst@lichuan.test")
    withdrawal = dataset.apply_withdrawal(ds, _withdraw(["S-1"], 2), "analyst@lichuan.test")
    assert withdrawal["counts"] == {"written": 0, "ignored": 0, "withdrawn": 1}
    latest = dataset.get_version(ds, 3)
    assert "S-1" not in {r["specimen_id"] for r in latest["rows"]}
    assert len(latest["withdrawn"]) == 1
    # 旧版本仍然可读取，不被撤回改写
    old = dataset.get_version(ds, 2)
    assert "S-1" in {r["specimen_id"] for r in old["rows"]}
    # 仅重发旧记录不能复活
    stale = dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,10,1\n", 3), "analyst@lichuan.test")
    assert stale["counts"]["written"] == 0
    assert stale["details"][0]["reason"] == "withdrawn_stale"
    assert "S-1" not in {r["specimen_id"] for r in dataset.get_version(ds, 4)["rows"]}
    # 有实质修订的新记录可以重新纳入
    readded = dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,15,1\n", 4), "analyst@lichuan.test")
    assert readded["counts"]["written"] == 1
    assert readded["details"][0]["reason"] == "readded"
    assert "S-1" in {r["specimen_id"] for r in dataset.get_version(ds, 5)["rows"]}


def test_unknown_withdrawal_id_rejects_whole_batch(fresh_dataset):
    ds = fresh_dataset
    before = repo.get("datasets", ds)
    with pytest.raises(ValueError):
        dataset.apply_withdrawal(ds, _withdraw(["S-1", "NOPE"], 1), "analyst@lichuan.test")
    assert repo.get("datasets", ds)["latest_revision"] == before["latest_revision"]


def test_concurrent_appends_equal_serial_application(fresh_dataset):
    ds = fresh_dataset
    batches = [
        ("specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-5,B,60,1\n", 1),
        ("specimen_id,group,duration_hours,event\nS-2,A,25,0\nS-6,A,70,0\n", 2),
        ("specimen_id,group,duration_hours,event\nS-5,B,65,1\n", 3),
    ]
    results = {}
    errors = []

    def worker(rev):
        try:
            results[rev] = dataset.apply_specimens(ds, _batch(batches[rev - 1][0], rev), "analyst@lichuan.test")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(rev,)) for rev in (3, 2, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert sorted(results) == [1, 2, 3]
    assert all(results[rev]["status"] == "applied" for rev in results)
    final = dataset.get_version(ds, repo.get("datasets", ds)["latest_version"])
    ids = {r["specimen_id"] for r in final["rows"]}
    assert ids == {"S-1", "S-2", "S-3", "S-4", "S-5", "S-6"}
    assert repo.get("datasets", ds)["latest_revision"] == 3
    assert len(dataset.list_batches(ds)) == 3


def test_retry_after_failure_does_not_double_accumulate(fresh_dataset):
    ds = fresh_dataset
    csv = "specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-7,B,80,1\n"
    dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    with pytest.raises(ValueError):
        # 合并后组别数超限的批次整批失败
        dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-8,C,10,1\nS-9,D,20,1\nS-10,E,30,1\nS-11,F,40,1\nS-12,G,50,1\nS-13,H,60,1\nS-14,I,70,1\n", 2), "analyst@lichuan.test")
    assert repo.get("datasets", ds)["latest_revision"] == 1
    result = dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    assert result["status"] == "replayed"
    assert repo.get("datasets", ds)["version_count"] == 2


def test_queued_run_locks_version_snapshot(fresh_dataset, monkeypatch):
    ds = fresh_dataset
    import src.services.run as run_service
    monkeypatch.setattr(run_service, "TASK_MODE", "manual")
    study = repo.get("studies", "study-process")
    # 为临时数据集建独立研究
    from src.models.entities import SurvivalStudy
    from src.utils.identity import new_id, now
    study2 = SurvivalStudy(new_id("study"), "t", ds, study["rule_id"], "A", "B", now())
    repo.put("studies", study2)
    queued = run.create(type("RunInput", (), {"study_id": study2.id, "dataset_version": ""})())
    version_before = repo.get("datasets", ds)["latest_version"]
    dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-2,A,20,0\nS-5,B,60,1\nS-6,B,90,0\n", version_before), "analyst@lichuan.test")
    assert repo.get("datasets", ds)["latest_version"] > version_before
    executed = run.execute(queued["id"])
    assert executed["status"] == "completed"
    assert executed["result"]["dataset"]["version"] == version_before
    assert executed["result"]["dataset"]["row_count"] == 4
    # 新运行使用最新资料
    new_run = run.create(type("RunInput", (), {"study_id": study2.id, "dataset_version": ""})())
    new_exec = run.execute(new_run["id"])
    assert new_exec["result"]["dataset"]["version"] > version_before


def test_runs_can_pin_old_version_for_continued_analysis(fresh_dataset, monkeypatch):
    ds = fresh_dataset
    import src.services.run as run_service
    monkeypatch.setattr(run_service, "TASK_MODE", "manual")
    study = repo.get("studies", "study-process")
    from src.models.entities import SurvivalStudy
    from src.utils.identity import new_id, now
    study2 = SurvivalStudy(new_id("study"), "t", ds, study["rule_id"], "A", "B", now())
    repo.put("studies", study2)
    old_version = repo.get("datasets", ds)["latest_version_id"]
    dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-1,A,500,1\n", 1), "analyst@lichuan.test")
    pinned = run.create(type("RunInput", (), {"study_id": study2.id, "dataset_version": old_version})())
    executed = run.execute(pinned["id"])
    assert executed["result"]["dataset"]["row_count"] == 4
    assert executed["result"]["dataset"]["version"] == 1


def test_http_batch_endpoints_and_history(client, token, fresh_dataset):
    headers = token
    r = client.post(f"/datasets/{fresh_dataset}/specimens", headers=headers, json={
        "revision": 1, "source": "b1.csv", "note": "补传",
        "csv_text": "specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-9,B,50,1\n",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["counts"] == {"written": 1, "ignored": 1, "withdrawn": 0}
    assert body["resulting_version"] == 2

    r = client.post(f"/datasets/{fresh_dataset}/withdrawals", headers=headers, json={
        "revision": 2, "source": "w.csv", "note": "归档撤回", "specimen_ids": ["S-2"],
    })
    assert r.status_code == 201
    assert r.json()["counts"]["withdrawn"] == 1

    r = client.post(f"/datasets/{fresh_dataset}/specimens", headers=headers, json={
        "revision": 1, "source": "b1.csv", "note": "重试",
        "csv_text": "specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-9,B,50,1\n",
    })
    assert r.status_code == 201 and r.json()["status"] == "replayed"

    r = client.post(f"/datasets/{fresh_dataset}/specimens", headers=headers, json={
        "revision": 1, "source": "b1.csv", "note": "冲突",
        "csv_text": "specimen_id,group,duration_hours,event\nS-9,B,55,1\n",
    })
    assert r.status_code == 409

    versions = client.get(f"/datasets/{fresh_dataset}/versions", headers=headers).json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[-1]["is_latest"] is True
    batches = client.get(f"/datasets/{fresh_dataset}/batches", headers=headers).json()
    assert [b["revision"] for b in batches] == [1, 2]
    assert batches[0]["replay_count"] == 1
    assert batches[0]["counts"] == {"written": 1, "ignored": 1, "withdrawn": 0}
    assert batches[0]["details"][0]["specimen_id"] == "S-1"

    detail = client.get(f"/datasets/{fresh_dataset}", headers=headers).json()
    assert detail["latest_revision"] == 2
    assert len(detail["versions"]) == 3
    assert len(detail["batches"]) == 2


def test_viewer_cannot_append(client, fresh_dataset):
    r = client.post("/auth/login", json={"email": "viewer@lichuan.test", "password": "Life2026!"})
    headers = {"Authorization": "Bearer " + r.json()["access_token"]}
    r = client.post(f"/datasets/{fresh_dataset}/specimens", headers=headers, json={
        "revision": 1, "csv_text": "specimen_id,group,duration_hours,event\nS-1,A,10,1\n"})
    assert r.status_code == 403


def test_failed_commit_leaves_no_partial_version_then_retry_applies_once(fresh_dataset, monkeypatch):
    ds = fresh_dataset
    csv = "specimen_id,group,duration_hours,event\nS-5,B,60,1\nS-6,A,70,0\n"
    import src.services.dataset as dataset_service
    real_put = dataset_service.repo.put_in

    def failing_put(conn, q, table, item):
        if table == 'dataset_versions':
            raise RuntimeError('store failure')
        return real_put(conn, q, table, item)

    monkeypatch.setattr(dataset_service.repo, 'put_in', failing_put)
    with pytest.raises(RuntimeError):
        dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    assert repo.get("datasets", ds)["latest_revision"] == 0
    assert repo.get("datasets", ds)["version_count"] == 1
    assert not [b for b in repo.all_rows("dataset_batches") if b["dataset_id"] == ds]

    monkeypatch.setattr(dataset_service.repo, 'put_in', real_put)
    result = dataset.apply_specimens(ds, _batch(csv, 1), "analyst@lichuan.test")
    assert result["status"] == "applied"
    assert result["counts"] == {"written": 2, "ignored": 0, "withdrawn": 0}
    latest = repo.get("datasets", ds)
    assert latest["latest_revision"] == 1
    assert latest["version_count"] == 2
    assert len(dataset.list_batches(ds)) == 1
    final = dataset.get_version(ds, 2)
    assert {r["specimen_id"] for r in final["rows"]} == {"S-1", "S-2", "S-3", "S-4", "S-5", "S-6"}


def test_run_pins_version_via_http(client, token, fresh_dataset, monkeypatch):
    import src.services.run as run_service
    monkeypatch.setattr(run_service, "TASK_MODE", "inline")
    study = repo.get("studies", "study-process")
    from src.models.entities import SurvivalStudy
    from src.utils.identity import new_id, now
    study2 = SurvivalStudy(new_id("study"), "t", fresh_dataset, study["rule_id"], "A", "B", now())
    repo.put("studies", study2)
    dataset.apply_specimens(fresh_dataset, _batch("specimen_id,group,duration_hours,event\nS-5,B,60,1\n", 1), "analyst@lichuan.test")
    r = client.post("/runs", headers=token, json={"study_id": study2.id})
    assert r.status_code == 201
    run = r.json()
    assert run["dataset_version"] == 2
    assert run["dataset_version_id"] == fresh_dataset + "-v2"
    assert run["status"] == "completed"
    assert run["result"]["dataset"]["row_count"] == 5
    assert run["result"]["dataset"]["version"] == 2
    # 结果固定：后续补传不改变已完成运行的快照指针
    dataset.apply_specimens(fresh_dataset, _batch("specimen_id,group,duration_hours,event\nS-6,A,70,0\n", 2), "analyst@lichuan.test")
    detail = client.get("/runs/" + run["id"], headers=token).json()
    assert detail["dataset_version"] == 2
    assert detail["result"]["dataset"]["row_count"] == 5


def test_viewer_can_read_batches_and_versions(client, fresh_dataset):
    r = client.post("/auth/login", json={"email": "viewer@lichuan.test", "password": "Life2026!"})
    headers = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.get(f"/datasets/{fresh_dataset}/versions", headers=headers).status_code == 200
    assert client.get(f"/datasets/{fresh_dataset}/batches", headers=headers).status_code == 200


def test_replay_of_old_revision_after_later_batches_does_not_rewind(fresh_dataset):
    ds = fresh_dataset
    v1 = "specimen_id,group,duration_hours,event\nS-1,A,10,1\nS-5,B,60,1\n"
    dataset.apply_specimens(ds, _batch(v1, 1), "analyst@lichuan.test")
    dataset.apply_specimens(ds, _batch("specimen_id,group,duration_hours,event\nS-6,A,70,0\n", 2), "analyst@lichuan.test")
    before = repo.get("datasets", ds)
    replay = dataset.apply_specimens(ds, _batch(v1, 1), "analyst@lichuan.test")
    assert replay["status"] == "replayed"
    after = repo.get("datasets", ds)
    assert after["latest_version_id"] == before["latest_version_id"]
    assert after["latest_revision"] == 2
    assert len(dataset.get_version(ds, 3)["rows"]) == 6
