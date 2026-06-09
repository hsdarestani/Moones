from enum import StrEnum


class Emotion(StrEnum):
    HAPPY = "happy"
    LONELY = "lonely"
    STRESSED = "stressed"
    BORED = "bored"
    EXCITED = "excited"
    NEUTRAL = "neutral"


_KEYWORDS = {
    Emotion.LONELY: ("lonely", "alone", "miss", "دلتنگ", "تنها"),
    Emotion.STRESSED: ("stress", "worried", "anxious", "خسته", "استرس", "نگران"),
    Emotion.HAPPY: ("happy", "great", "خوشحال", "عالی"),
    Emotion.BORED: ("bored", "boring", "حوصله", "کسل"),
    Emotion.EXCITED: ("excited", "can't wait", "هیجان", "ذوق"),
}


def detect_emotion(text: str) -> Emotion:
    lowered = text.lower()
    for emotion, keywords in _KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return emotion
    return Emotion.NEUTRAL
