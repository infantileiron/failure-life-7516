import os, sys, tempfile
from pathlib import Path

# 必须在导入 src 之前设置环境，仓库模块在导入时读取配置。
_DB = Path(tempfile.gettempdir()) / "failure-life-tests.sqlite"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = "sqlite:///" + str(_DB)
os.environ["SEED_DEMO"] = "false"
os.environ["TASK_MODE"] = "manual"
os.environ["ARTIFACT_DIR"] = str(Path(tempfile.gettempdir()) / "failure-life-test-artifacts")
os.environ["BATCH_REVISION_WAIT_SECONDS"] = "5"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
