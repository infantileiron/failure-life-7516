import time
from dataclasses import asdict

from fastapi import HTTPException

from src.config.settings import BATCH_REVISION_WAIT_SECONDS
from src.constants.domain import GROUP_LIMIT, MAX_SPECIMENS
from src.models.entities import DatasetBatch, DatasetVersion, SpecimenDataset
from src.services.specimen_csv import (
    canonical_checksum, content_hash, parse_csv, row_content_hash, row_key,
)
from src.utils import repository as repo
from src.utils.identity import new_id, now

# ---------- 读取 ----------

def summary(item):
    rows = item.get('rows', [])
    out = {k: v for k, v in item.items() if k not in ('rows', 'specimen_revisions', 'row_history', 'withdrawn')}
    out |= {
        'row_count': len(rows),
        'groups': sorted({r['group'] for r in rows}),
        'events': sum(r['event'] for r in rows),
        'censored': sum(1 - r['event'] for r in rows),
        'latest_version': item.get('latest_version', 1),
        'latest_version_id': item.get('latest_version_id', ''),
        'latest_revision': item.get('latest_revision', 0),
        'version_count': item.get('version_count', 1),
        'batch_count': item.get('batch_count', 0),
        'withdrawn_count': len(item.get('withdrawn', [])),
    }
    return out


def version_summary(version, dataset=None):
    rows = version['rows']
    return {
        'id': version['id'],
        'dataset_id': version['dataset_id'],
        'version': version['version'],
        'revision': version['revision'],
        'batch_id': version.get('batch_id', ''),
        'source': version.get('source', ''),
        'checksum': version['checksum'],
        'created_at': version.get('created_at', ''),
        'row_count': len(rows),
        'groups': sorted({r['group'] for r in rows}),
        'events': sum(r['event'] for r in rows),
        'censored': sum(1 - r['event'] for r in rows),
        'withdrawn_count': len(version.get('withdrawn', [])),
        'is_latest': bool(dataset) and version['id'] == dataset.get('latest_version_id'),
    }


def batch_summary(batch):
    return {k: v for k, v in batch.items() if k != 'content_hash'} | {
        'content_hash': batch.get('content_hash', '')[:16],
        'entry_count': len(batch.get('details', [])),
    }


def _sorted_versions(dataset_id):
    # 版本号是数值，不能按 TEXT 主键排序（否则 v10 会排在 v2 之前）。
    return sorted((v for v in repo.all_rows('dataset_versions') if v['dataset_id'] == dataset_id),
                  key=lambda v: v['version'])


def list_datasets():
    return [summary(x) | {'versions': _version_list(x['id'])} for x in repo.all_rows('datasets')]


def detail(id):
    item = repo.get('datasets', id)
    return item | {'versions': _version_list(id), 'batches': list_batches(id)}


def _version_list(dataset_id):
    dataset = repo.get('datasets', dataset_id)
    return [version_summary(v, dataset) for v in _sorted_versions(dataset_id)]


def list_versions(dataset_id):
    repo.get('datasets', dataset_id)
    return _version_list(dataset_id)


def get_version(dataset_id, version):
    repo.get('datasets', dataset_id)
    for v in _sorted_versions(dataset_id):
        if v['version'] == version:
            return v
    raise HTTPException(404, '资料版本不存在')


def list_batches(dataset_id):
    repo.get('datasets', dataset_id)
    batches = [b for b in repo.all_rows('dataset_batches') if b['dataset_id'] == dataset_id]
    return [batch_summary(b) for b in sorted(batches, key=lambda b: b['revision'])]


def latest_rows(dataset_id, version_id=''):
    """返回供研究/运行使用的不可变资料行。"""
    if version_id:
        try:
            return repo.get('dataset_versions', version_id)
        except HTTPException:
            raise ValueError('指定资料版本不存在')
    dataset = repo.get('datasets', dataset_id)
    if dataset.get('latest_version_id'):
        try:
            return repo.get('dataset_versions', dataset['latest_version_id'])
        except HTTPException:
            pass
    return dataset


# ---------- 整表新建 ----------

def create(data):
    rows = parse_csv(data.csv_text, minimum=2, maximum=MAX_SPECIMENS)
    if len({r['group'] for r in rows}) > GROUP_LIMIT:
        raise ValueError('最多8个组别')
    dataset = SpecimenDataset(new_id('ds'), data.name, data.source, rows, now())
    return _create_with_version(asdict(dataset))


def _create_with_version(dataset):
    dataset['latest_version'] = 1
    dataset['latest_revision'] = 0
    dataset['version_count'] = 1
    dataset['batch_count'] = 0
    dataset['specimen_revisions'] = {r['specimen_id']: 0 for r in dataset['rows']}
    dataset['row_history'] = {r['specimen_id']: [row_content_hash(r)] for r in dataset['rows']}
    dataset['withdrawn'] = []
    dataset['latest_version_id'] = dataset['id'] + '-v1'
    dataset.setdefault('name', dataset['id'])
    dataset.setdefault('source', '')
    dataset.setdefault('created_at', now())
    dataset.setdefault('updated_at', dataset.get('created_at', now()))
    dataset['data_checksum'] = canonical_checksum(dataset['rows'], dataset['specimen_revisions'], dataset['withdrawn'])
    version = DatasetVersion(
        id=dataset['latest_version_id'], dataset_id=dataset['id'], version=1, revision=0,
        rows=dataset['rows'], specimen_revisions=dataset['specimen_revisions'], withdrawn=[],
        source=dataset['source'], batch_id='', checksum=dataset['data_checksum'],
        created_at=dataset['created_at'],
    )
    repo.put('dataset_versions', version)
    repo.put('datasets', dataset)
    return summary(dataset)


def rename(id, data):
    item = repo.get('datasets', id)
    item['name'] = data.name
    repo.put('datasets', item)
    return summary(item)


# ---------- 增量补传 / 撤回 ----------

def apply_specimens(dataset_id, data, actor):
    rows = parse_csv(data.csv_text, minimum=1, maximum=MAX_SPECIMENS)
    return _run_batch(dataset_id, 'specimen', data.revision, data.source, data.note, actor,
                      rows=rows, withdrawn_ids=())


def apply_withdrawal(dataset_id, data, actor):
    ids = list(data.specimen_ids)
    if not ids:
        raise ValueError('撤回批次至少需要一个试件编号')
    if len(ids) != len(set(ids)):
        raise ValueError('撤回批次内试件编号不能重复')
    return _run_batch(dataset_id, 'withdrawal', data.revision, data.source or 'withdrawals', data.note, actor,
                      rows=(), withdrawn_ids=ids)


class _RetryRevision(Exception):
    pass


def _run_batch(dataset_id, kind, revision, source, note, actor, rows=(), withdrawn_ids=()):
    fingerprint = content_hash(kind, rows, withdrawn_ids)
    deadline = time.monotonic() + BATCH_REVISION_WAIT_SECONDS
    while True:
        try:
            return _attempt_batch(dataset_id, kind, revision, source, note, actor,
                                  rows, withdrawn_ids, fingerprint, deadline)
        except _RetryRevision:
            # 并发补传按修订号排序：后一个修订在前一个落库前不抢跑，
            # 释放锁等待后重试，最终结果等同逐批串行。
            time.sleep(0.05)


def _attempt_batch(dataset_id, kind, revision, source, note, actor, rows, withdrawn_ids, fingerprint, deadline):
    with repo.transaction() as (conn, q):
        dataset = repo.find_in(conn, q, 'datasets', dataset_id)
        if dataset is None:
            raise HTTPException(404, '记录不存在')
        existing = repo.find_in(conn, q, 'dataset_batches', _batch_id(dataset_id, revision))
        if existing is not None:
            if existing['content_hash'] != fingerprint:
                raise HTTPException(409, f'修订号 {revision} 已存在但内容不同：同版异内容整批不生效，请改用新的递增修订号')
            existing['replay_count'] = existing.get('replay_count', 0) + 1
            existing['last_replay_at'] = now()
            repo.put_in(conn, q, 'dataset_batches', existing)
            return _response(existing, 'replayed')
        latest = dataset.get('latest_revision', 0)
        if revision != latest + 1:
            if revision > latest + 1 and time.monotonic() < deadline:
                raise _RetryRevision()
            raise HTTPException(409, f'修订号已过时或跳号：当前已到 {latest}，下一批应为 {latest + 1}')
        if kind == 'specimen':
            batch, dataset, version = _merge_rows(dataset, revision, fingerprint, rows, source, note, actor)
        else:
            batch, dataset, version = _withdraw_rows(dataset, revision, fingerprint, withdrawn_ids, source, note, actor)
        repo.put_in(conn, q, 'datasets', dataset)
        repo.put_in(conn, q, 'dataset_versions', version)
        repo.put_in(conn, q, 'dataset_batches', batch)
        return _response(batch, 'applied')


def _merge_rows(dataset, revision, fingerprint, rows, source, note, actor):
    current = {r['specimen_id']: r for r in dataset['rows']}
    revisions = dict(dataset.get('specimen_revisions', {}))
    history = {k: list(v) for k, v in dataset.get('row_history', {}).items()}
    tombstones = {w['specimen_id']: w for w in dataset.get('withdrawn', [])}
    updated, details, written, ignored = {}, [], 0, 0
    for row in rows:
        sid = row['specimen_id']
        digest = row_content_hash(row)
        tombstone = tombstones.get(sid)
        if tombstone is not None:
            if digest in tombstone.get('contents', []):
                # 旧记录（撤回前已见过的内容）不能使试件复活。
                ignored += 1
                details.append({'specimen_id': sid, 'action': 'ignored', 'revision': revision,
                                'reason': 'withdrawn_stale'})
                continue
            tombstone = dict(tombstone)
            tombstone['reactivated_revision'] = revision
            history[sid] = list(tombstone.get('contents', [])) + [digest]
            tombstones.pop(sid)
            updated[sid] = row
            revisions[sid] = revision
            written += 1
            details.append({'specimen_id': sid, 'action': 'written', 'revision': revision, 'reason': 'readded'})
            continue
        previous = current.get(sid)
        if previous is not None and row_key(previous) == row_key(row):
            ignored += 1
            details.append({'specimen_id': sid, 'action': 'ignored', 'revision': revision, 'reason': 'unchanged'})
            continue
        updated[sid] = row
        revisions[sid] = revision
        history.setdefault(sid, []).append(digest)
        written += 1
        details.append({'specimen_id': sid, 'action': 'written', 'revision': revision,
                        'reason': 'corrected' if previous is not None else 'new'})
    output = []
    for current_row in dataset['rows']:
        sid = current_row['specimen_id']
        if sid in updated:
            output.append(updated.pop(sid))
        elif sid in tombstones:
            continue
        else:
            output.append(current_row)
    for sid in updated:
        output.append(updated[sid])
    counts = {'written': written, 'ignored': ignored, 'withdrawn': 0}
    return _commit_batch(dataset, revision, fingerprint, source, note, actor,
                         kind='specimen', rows_out=output, revisions=revisions, history=history,
                         tombstones=list(tombstones.values()), counts=counts, details=details)


def _withdraw_rows(dataset, revision, fingerprint, specimen_ids, source, note, actor):
    current = {r['specimen_id']: r for r in dataset['rows']}
    revisions = dict(dataset.get('specimen_revisions', {}))
    history = {k: list(v) for k, v in dataset.get('row_history', {}).items()}
    tombstones = {w['specimen_id']: w for w in dataset.get('withdrawn', [])}
    details, withdrawn, ignored = [], 0, 0
    for sid in specimen_ids:
        if sid not in current and sid not in tombstones:
            raise ValueError(f'试件编号 {sid} 不存在，撤回批次整批不生效')
        if sid in tombstones:
            ignored += 1
            details.append({'specimen_id': sid, 'action': 'ignored', 'revision': revision,
                            'reason': 'already_withdrawn'})
            continue
        previous = current[sid]
        contents = tombstones.get(sid, {}).get('contents') or history.get(sid, [])
        tombstones[sid] = {'specimen_id': sid, 'revision': revision,
                           'contents': list(contents), 'row': previous,
                           'withdrawn_at': now()}
        revisions.pop(sid, None)
        withdrawn += 1
        details.append({'specimen_id': sid, 'action': 'withdrawn', 'revision': revision,
                        'reason': 'withdrawn'})
    output = [r for r in dataset['rows'] if r['specimen_id'] not in tombstones]
    counts = {'written': 0, 'ignored': ignored, 'withdrawn': withdrawn}
    return _commit_batch(dataset, revision, fingerprint, source, note, actor,
                         kind='withdrawal', rows_out=output, revisions=revisions, history=history,
                         tombstones=list(tombstones.values()), counts=counts, details=details)


def _commit_batch(dataset, revision, fingerprint, source, note, actor, kind,
                  rows_out, revisions, history, tombstones, counts, details):
    if len(rows_out) > MAX_SPECIMENS:
        raise ValueError(f'合并后超过{MAX_SPECIMENS}件试件，整批不生效')
    if len({r['group'] for r in rows_out}) > GROUP_LIMIT:
        raise ValueError('合并后超过8个组别，整批不生效')
    version_number = dataset.get('version_count', 1) + 1
    version_id = f"{dataset['id']}-v{version_number}"
    checksum = canonical_checksum(rows_out, revisions, tombstones)
    created = now()
    batch = DatasetBatch(
        id=_batch_id(dataset['id'], revision), dataset_id=dataset['id'], revision=revision,
        content_hash=fingerprint, status='applied', kind=kind, source=source, note=note,
        actor=actor, counts=counts, details=details, resulting_version=version_number,
        dataset_version_id=version_id, replay_count=0, created_at=created,
    )
    version = DatasetVersion(
        id=version_id, dataset_id=dataset['id'], version=version_number, revision=revision,
        rows=rows_out, specimen_revisions=revisions, withdrawn=tombstones, source=source,
        batch_id=batch.id, checksum=checksum, created_at=created,
    )
    dataset.update({
        'rows': rows_out, 'specimen_revisions': revisions, 'row_history': history,
        'withdrawn': tombstones, 'latest_version': version_number, 'latest_version_id': version_id,
        'latest_revision': revision, 'version_count': version_number,
        'batch_count': dataset.get('batch_count', 0) + 1, 'updated_at': created,
        'data_checksum': checksum,
    })
    return batch, dataset, version


def _batch_id(dataset_id, revision):
    return f'{dataset_id}-b{revision}'


def _response(batch, status):
    batch = asdict(batch) if not isinstance(batch, dict) else dict(batch)
    entries = len(batch['details'])
    counts = ({'written': 0, 'ignored': entries, 'withdrawn': 0}
              if status == 'replayed' else batch['counts'])
    return {
        'status': status,
        'replay': status == 'replayed',
        'revision': batch['revision'],
        'resulting_version': batch['resulting_version'],
        'dataset_version_id': batch['dataset_version_id'],
        'counts': counts,
        'original_counts': batch['counts'],
        'details': batch['details'],
        'replay_count': batch.get('replay_count', 0),
        'batch': batch,
    }