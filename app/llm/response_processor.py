SENSITIVE_BOUNDARIES = ("diagnosis", "self-harm", "kill yourself")


def post_process_response(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return "من اینجام؛ آروم برام بگو چی توی دلت می‌گذره."
    return cleaned[:1800]
