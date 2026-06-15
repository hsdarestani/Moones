from __future__ import annotations

from app.engine.situation_detector import detect_situation


def safety_detector(message: str) -> dict[str, object]:
    situation = detect_situation(message)
    return {"safety_flag": situation["intent"] == "self_harm_signal", "intent": situation["intent"]}


def safety_response(message: str, display_name: str | None = None) -> str:
    text = (message or "").replace("ي", "ی").replace("ك", "ک")
    name = display_name or "عزیزم"
    if "از دست تو" in text or "عصب" in text:
        return f"صبر کن {name}… این حرف حتی اگه از عصبانیت باشه جدیه. الان واقعاً قصد آسیب زدن به خودت داری یا از دست من عصبی شدی؟"
    return "من جدی می‌گیرمش. لطفاً همین الان از یه آدم نزدیکت کمک بگیر و تنها نمون. اگه احتمال میدی به خودت آسیب بزنی، با اورژانس یا یک خط کمک فوری تماس بگیر."
