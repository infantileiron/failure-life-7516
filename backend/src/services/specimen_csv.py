import csv, hashlib, io, json, math
from src.constants.domain import MAX_SPECIMENS

REQUIRED_COLUMNS = {'specimen_id', 'group', 'duration_hours', 'event'}

def parse_csv(text, minimum=1, maximum=MAX_SPECIMENS):
    """解析并校验试件 CSV；任何一行不合法整批拒绝。"""
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')))
    fields = reader.fieldnames or []
    if not REQUIRED_COLUMNS.issubset(fields):
        raise ValueError('CSV需要specimen_id,group,duration_hours,event；可含censored=1-event')
    rows = []
    seen = set()
    for n, r in enumerate(reader, 2):
        if n > maximum + 1:
            raise ValueError(f'最多{maximum}试件')
        specimen_id = (r.get('specimen_id') or '').strip()
        group = (r.get('group') or '').strip()
        try:
            duration = float(r.get('duration_hours') or 'nan')
            event = r.get('event')
            if (not math.isfinite(duration) or not 0 < duration <= 10000000
                    or event not in ('0', '1') or not specimen_id or specimen_id in seen
                    or not 1 <= len(group) <= 30):
                raise ValueError()
            event = int(event)
            if 'censored' in fields and r.get('censored') != str(1 - event):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f'第{n}行时间/事件/删失/组别/试件编号无效或重复')
        seen.add(specimen_id)
        rows.append({'specimen_id': specimen_id, 'group': group, 'duration_hours': duration, 'event': event, 'censored': 1 - event})
    if len(rows) < minimum:
        raise ValueError(f'至少{minimum}个试件')
    return rows

def row_key(row):
    """用于判重的试件内容；不比较派生字段。"""
    return (row['group'], float(row['duration_hours']), int(row['event']))

def row_content_hash(row):
    """单条试件内容指纹，用于撤回后识别“旧记录”。"""
    return hashlib.sha256(
        f"{row['group']}|{float(row['duration_hours'])}|{int(row['event'])}".encode()
    ).hexdigest()[:16]

def content_hash(kind, rows=(), withdrawn_ids=()):
    """批次幂等指纹：只由该批提交的试件内容与撤回编号决定。"""
    payload = {
        'kind': kind,
        'specimens': sorted([[r['specimen_id'], r['group'], float(r['duration_hours']), int(r['event'])] for r in rows]),
        'withdrawn': sorted(withdrawn_ids),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def canonical_checksum(rows, revisions=None, withdrawn=()):
    payload = {
        'rows': sorted([[r['specimen_id'], r['group'], float(r['duration_hours']), int(r['event'])] for r in rows]),
        'revisions': sorted((revisions or {}).items()),
        'withdrawn': sorted(w['specimen_id'] for w in withdrawn),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
