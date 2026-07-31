import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("database_url", "sqlite:///:memory:")
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_collection_modifyitems(session, config, items):
    from app.services.live_failed_image_job_probe import run_live_failed_image_job_probe
    run_live_failed_image_job_probe()
