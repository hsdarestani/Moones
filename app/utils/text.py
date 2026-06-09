def truncate(text: str, limit: int = 1800) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
