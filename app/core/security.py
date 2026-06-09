import hmac
from hashlib import sha256


def verify_telegram_secret(received: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if not received:
        return False
    return hmac.compare_digest(received, expected)


def stable_user_hash(raw_id: int | str, secret_key: str) -> str:
    digest = hmac.new(secret_key.encode(), str(raw_id).encode(), sha256).hexdigest()
    return digest[:32]
