import os
from pathlib import Path
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/failure-life.sqlite")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "failure-life-development-secret-change-this")
SEED_DEMO = os.getenv("SEED_DEMO", "true").lower() == "true"
TASK_MODE = os.getenv("TASK_MODE", "redis")
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "/tmp/failure-life-artifacts"))
# 并发补传带连续修订号时，最多等待前序修订落库的秒数。
BATCH_REVISION_WAIT_SECONDS = float(os.getenv("BATCH_REVISION_WAIT_SECONDS", "10"))
