import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("database_url", "sqlite:///:memory:")

# Most of the historical suite exercises media and purchasable-addon internals.
# Keep those tests in legacy mode; product-mode behavior has dedicated tests.
os.environ.setdefault("TEXT_ONLY_MODE", "false")
os.environ.setdefault("ADULT_CHAT_DEFAULT", "false")
os.environ.setdefault("ADULT_CHAT_MAX_INTIMACY", "false")
os.environ.setdefault("IMAGE_INPUT_ENABLED", "true")
os.environ.setdefault("IMAGE_GENERATION_ENABLED", "true")
os.environ.setdefault("VOICE_INPUT_ENABLED", "true")
os.environ.setdefault("VENICE_TTS_ENABLED", "true")

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
