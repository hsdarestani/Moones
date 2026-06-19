import logging
import re
from typing import Any

_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_DB_URL_RE = re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s]+")


def mask_secrets(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = _TOKEN_RE.sub("bot<redacted>", value)
    return _DB_URL_RE.sub("postgresql://<redacted>", value)


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(mask_secrets(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: mask_secrets(value) for key, value in record.args.items()}
        return True


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    root_logger = logging.getLogger()
    if not any(isinstance(existing, SecretMaskingFilter) for existing in root_logger.filters):
        root_logger.addFilter(SecretMaskingFilter())
    for handler in root_logger.handlers:
        if not any(isinstance(existing, SecretMaskingFilter) for existing in handler.filters):
            handler.addFilter(SecretMaskingFilter())
