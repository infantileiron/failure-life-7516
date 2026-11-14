import json, sqlite3, threading
from contextlib import contextmanager
from dataclasses import asdict
from fastapi import HTTPException
from src.config.settings import DATABASE_URL
TABLES = ("datasets", "dataset_versions", "dataset_batches", "rules", "studies", "runs", "reviews", "reports", "users", "audit")
_LOCK = threading.RLock()
# 所有会改动“资料版本”的读改写都在同一把数据库级锁内完成，
# 因此并发补传与排队取快照等价于逐批串行。
WRITE_LOCK_KEY = 2301
def connect():
    if DATABASE_URL.startswith("sqlite:///"):
        conn = sqlite3.connect(DATABASE_URL.removeprefix("sqlite:///"), timeout=30)
        return conn, "?"
    import psycopg
    return psycopg.connect(DATABASE_URL), "%s"
def init_db():
    conn,_ = connect()
    try:
        if not DATABASE_URL.startswith("sqlite:///"):
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,))
        for table in TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.commit()
    finally: conn.close()
def all_rows(table):
    assert table in TABLES
    conn,_ = connect()
    try: return [json.loads(row[0]) for row in conn.execute(f"SELECT payload FROM {table} ORDER BY id").fetchall()]
    finally: conn.close()
def get(table, id):
    assert table in TABLES
    conn,q = connect()
    try:
        row = conn.execute(f"SELECT payload FROM {table} WHERE id={q}", (id,)).fetchone()
        if row is None: raise HTTPException(404, "记录不存在")
        return json.loads(row[0])
    finally: conn.close()
def put(table, item, insert_only=False):
    assert table in TABLES
    if not isinstance(item, dict): item = asdict(item)
    conn,q = connect()
    clause = "DO NOTHING" if insert_only else "DO UPDATE SET payload=excluded.payload"
    try:
        with _LOCK:
            conn.execute(f"INSERT INTO {table} (id,payload) VALUES ({q},{q}) ON CONFLICT(id) {clause}", (item["id"], _dumps(item)))
            conn.commit()
        return item
    finally: conn.close()
@contextmanager
def transaction():
    """串行化的读改写事务。

    SQLite 使用 BEGIN IMMEDIATE 取得写锁；PostgreSQL 使用事务级 advisory lock。
    进程内再加 RLock，避免同一进程多线程在 SQLite 上互相等待超时。
    """
    conn,q = connect()
    sqlite = DATABASE_URL.startswith("sqlite:///")
    try:
        with _LOCK:
            if sqlite:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,))
            try:
                yield conn,q
            except BaseException:
                try:
                    conn.execute("ROLLBACK") if sqlite else conn.rollback()
                except Exception: pass
                raise
            else:
                conn.execute("COMMIT") if sqlite else conn.commit()
    finally: conn.close()
def find_in(conn,q,table,id):
    assert table in TABLES
    row = conn.execute(f"SELECT payload FROM {table} WHERE id={q}", (id,)).fetchone()
    return json.loads(row[0]) if row is not None else None
def all_in(conn,q,table):
    assert table in TABLES
    return [json.loads(row[0]) for row in conn.execute(f"SELECT payload FROM {table} ORDER BY id").fetchall()]
def put_in(conn,q,table,item):
    assert table in TABLES
    if not isinstance(item, dict): item = asdict(item)
    conn.execute(f"INSERT INTO {table} (id,payload) VALUES ({q},{q}) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (item["id"], _dumps(item)))
    return item
def put_new_in(conn,q,table,item):
    """只插入不覆盖，用于不可变版本与幂等批次记录。"""
    assert table in TABLES
    if not isinstance(item, dict): item = asdict(item)
    conn.execute(f"INSERT INTO {table} (id,payload) VALUES ({q},{q}) ON CONFLICT(id) DO NOTHING", (item["id"], _dumps(item)))
    return item
def _dumps(item):
    return json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
