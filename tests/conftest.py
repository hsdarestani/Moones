import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("database_url", "sqlite:///:memory:")
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_collection_modifyitems(session, config, items):
    if not str(os.environ.get("VENICE_API_KEY") or os.environ.get("venice_api_key") or "").strip():
        return
    os.environ["MOONES_LIVE_PARTNER_WORKER_PROBE"] = "1"
    from app.services.live_partner_worker_probe import run_live_partner_worker_probe_if_configured
    run_live_partner_worker_probe_if_configured()
