import json
from dataclasses import asdict

from fastapi import HTTPException

from src.config.settings import ARTIFACT_DIR, REDIS_URL, TASK_MODE
from src.models.entities import SurvivalRun
from src.services.dataset import latest_rows
from src.services.statistics import analyze
from src.utils import repository as repo
from src.utils.identity import new_id, now


def list_runs():
    return repo.all_rows("runs")


def execute(id):
    run = repo.get("runs", id)
    if run["status"] in ("running", "completed"):
        return run
    run["status"] = "running"
    repo.put("runs", run)
    try:
        study = repo.get("studies", run["study_id"])
        rule = repo.get("rules", study["rule_id"])
        # 只读运行创建时锁定的不可变版本；排队期间的补传不会混入本次计算。
        dataset = latest_rows(study["dataset_id"], run.get("dataset_version_id", ""))
        run["result"] = analyze(dataset["rows"], rule, study, dataset)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        (ARTIFACT_DIR / (id + ".json")).write_text(json.dumps(run["result"], ensure_ascii=False))
        run["status"] = "completed"
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
    return repo.put("runs", run)


def create(data):
    with repo.transaction() as (conn, q):
        study = repo.find_in(conn, q, "studies", data.study_id)
        if study is None:
            raise HTTPException(404, "记录不存在")
        if sum(r["status"] in ("queued", "running") for r in repo.all_in(conn, q, "runs")) >= 10:
            raise ValueError("运行队列已满，请等待已有任务完成")
        dataset = repo.find_in(conn, q, "datasets", study["dataset_id"])
        if dataset is None:
            raise HTTPException(404, "记录不存在")
        if data.dataset_version:
            version = repo.find_in(conn, q, "dataset_versions", data.dataset_version)
            if version is None or version["dataset_id"] != dataset["id"]:
                raise ValueError("指定的资料版本不存在")
        elif dataset.get("latest_version_id"):
            version = repo.find_in(conn, q, "dataset_versions", dataset["latest_version_id"])
            if version is None:
                raise ValueError("资料版本快照缺失，请重新补传或联系管理员")
        else:
            raise ValueError("资料尚未建立版本快照，请先补传或重新导入")
        run = SurvivalRun(
            new_id("run"), data.study_id, created_at=now(),
            dataset_id=dataset["id"], dataset_version_id=version["id"],
            dataset_version=version["version"], dataset_revision=version["revision"],
            dataset_checksum=version["checksum"],
        )
        repo.put_in(conn, q, "runs", run)
    if TASK_MODE == "inline":
        return execute(run.id)
    if TASK_MODE != "redis":
        # manual/离线模式：运行保持排队，由 worker 或运维显式执行。
        return asdict(run)
    try:
        import redis
        redis.Redis.from_url(REDIS_URL).rpush("failure-life:jobs", run.id)
    except Exception:
        run.status = "failed"
        run.error = "队列不可用，请恢复Redis后重新提交"
        repo.put("runs", run)
        raise ValueError(run.error)
    return asdict(run)
