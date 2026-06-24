import os
import subprocess
import sys

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts.check_db_password_sync import parse_database_url, validate_password_sync

SECRET = "super-secret-password"
DATABASE_URL = f"postgresql+psycopg://postgres:{SECRET}@postgres:5432/mones"


def test_database_url_parser_extracts_password_safely():
    parts = parse_database_url(DATABASE_URL)
    assert parts.username == "postgres"
    assert parts.password == SECRET
    assert parts.hostname == "postgres"
    assert parts.database == "mones"


def test_password_sync_validation_requires_matching_values():
    validate_password_sync({"DATABASE_URL": DATABASE_URL, "DB_PASSWORD": SECRET, "POSTGRES_PASSWORD": SECRET})
    with pytest.raises(ValueError, match="does not match"):
        validate_password_sync({"DATABASE_URL": DATABASE_URL, "DB_PASSWORD": "different"})


def test_password_check_script_does_not_print_secret_values():
    env = os.environ.copy()
    env.update({"DATABASE_URL": DATABASE_URL, "DB_PASSWORD": SECRET, "POSTGRES_PASSWORD": SECRET})
    result = subprocess.run(
        [sys.executable, "scripts/check_db_password_sync.py"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert SECRET not in combined
    assert "<redacted>" in combined


def test_alembic_has_single_head_after_proactive_timing():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    assert heads == ["0020_patch13_natural_proactive"]
    merge_revision = script.get_revision("0015_merge_mood_recovery_and_proactive")
    assert set(merge_revision.down_revision) == {
        "0014_proactive_messages",
        "0013_mood_recovery_stage_normalization",
    }

from app.core.logger import mask_secrets


def test_log_secret_masking_redacts_database_urls_and_telegram_tokens():
    token = "bot123456:ABCDEF_secret-token"
    db_url = f"postgresql+psycopg://postgres:{SECRET}@postgres:5432/mones"
    masked = mask_secrets(f"POST {token}/sendMessage db={db_url}")
    assert token not in masked
    assert SECRET not in masked
    assert "bot<redacted>" in masked
    assert "postgresql://<redacted>" in masked
