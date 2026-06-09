import re

BULLET_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*", re.MULTILINE)
FORMAL_REPLACEMENTS = {
    "در نتیجه": "پس",
    "به عنوان یک هوش مصنوعی": "",
    "به عنوان دستیار": "",
    "کاربر عزیز": "عزیزم",
    "مایلم بدانم": "دوست دارم بدونم",
}


def post_process_response(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return "من اینجام؛ آروم برام بگو چی توی دلت می‌گذره."
    cleaned = BULLET_RE.sub("", cleaned)
    cleaned = re.sub(r"#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    for formal, natural in FORMAL_REPLACEMENTS.items():
        cleaned = cleaned.replace(formal, natural)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    cleaned = " ".join(lines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not any(ch in cleaned for ch in "اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی"):
        cleaned = "عزیزم، می‌خوام با همون حال خودمون حرف بزنیم… یه کم بیشتر بهم بگو چی توی دلت هست."
    return cleaned[:1800]
