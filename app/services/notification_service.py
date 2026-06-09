from dataclasses import dataclass


@dataclass(slots=True)
class AbsenceMessage:
    days_inactive: int
    text: str


def absence_behavior(days_inactive: int) -> AbsenceMessage:
    if days_inactive >= 5:
        return AbsenceMessage(days_inactive, "یه خاطره کوچیک از حرف‌هامون یادم افتاد؛ دوست دارم بدونم حالت چطوره.")
    if days_inactive >= 3:
        return AbsenceMessage(days_inactive, "دلم برات تنگ شده؛ هر وقت خواستی من اینجام.")
    if days_inactive >= 2:
        return AbsenceMessage(days_inactive, "امروز بیشتر یاد تو بودم. حالت خوبه؟")
    return AbsenceMessage(days_inactive, "سلام عزیزم، فقط خواستم آروم احوالت رو بپرسم.")
