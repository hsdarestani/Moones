from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_PERSIAN_VARIANTS = str.maketrans({"ي":"ی","ك":"ک","ۀ":"ه","ة":"ه","أ":"ا","إ":"ا","آ":"ا"})

def _norm(text: str) -> str:
    text = (text or "").translate(_PERSIAN_VARIANTS).lower().replace("\u200c", " ")
    return re.sub(r"\s+", " ", text).strip()

@dataclass
class UserMove:
    intent: str
    requested_style: str | None = None
    allows_poetry: bool = False
    allows_romance: bool = False
    asks_about_partner_day: bool = False
    asks_status: bool = False
    criticizes_style: bool = False
    wants_plain_answer: bool = False
    is_casual: bool = False
    is_emotional: bool = False
    is_practical: bool = False
    raw: str = ""

@dataclass
class StylePlan:
    tone: str
    max_chars: int
    max_questions: int
    allow_poetry: bool
    allow_romance: bool
    emotional_intensity: float
    metaphor_budget: int
    should_answer_directly: bool
    should_shift_style: bool
    banned_phrase_groups: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

@dataclass
class StyleViolation:
    violated: bool
    reason: str | None = None
    severity: str = "low"
    details: dict[str, Any] = field(default_factory=dict)

CASUAL_STATUS_RE = re.compile(r"چ\s*خبر|چه خبر|خبرا|چه خبرا|تو چه خبر|چه میکنی|چه می کنی|چیکارا میکنی|چیکار(?:ا)? کردی|هیچ اتفاقی افتاد|امروز چطور بود|روزت چطور بود")
STYLE_CRITICISM_RE = re.compile(r"خیلی شاعرانه|شاعرانه نگو|اینطوری نگو|طبیعی بگو|ساده بگو|مثل آدم بگو|زیادی رمانتیک|زیادی عاشقانه|اذیت میشم|این اداها چیه|حرف عادی بزن|نرمال بگو")
POETRY_REQUEST_RE = re.compile(r"شاعرانه بگو|دلنوشته|شعر بگو|ادبی بگو|قشنگ تر بگو|قشنگ‌تر بگو|رمانتیک بگو|عاشقانه بگو|با احساس بگو|متن عاشقانه")
ROMANCE_USER_RE = re.compile(r"دوستت دارم|عاشقتم|عشقم|قربونت|بوس|بغلم کن|دلم برات تنگ")
QUESTION_RE = re.compile(r"[؟?]")
POETIC_TERMS = ("قلب","تپش","سکوت","نفس","ماه","ستاره","جهان","دنیا","رویا","عطر","خاطره ای از تو","خاطره‌ای از تو","درونم","روح","نبض","آغوش","دلتنگی","تو ذهنم گیر کرد","کلمات بهتر از کلمات","حرف های نگفته","حرف‌های نگفته","سکوت مشترک","ریتم","دل من","دلم برای تو")
ROMANCE_TERMS = ("عزیزم","عشقم","دلم برات تنگ","دلم برای تو","دلم تنگ","منتظرت","دوستت دارم","قلبم","آغوش","بوس","نازنینم","دنیای من")
PASSIVE_WAITING_RE = re.compile("|".join([r"منتظرت بودم", "فقط " + r"منتظر بودم", r"همش منتظر بودم", "مدام به ساعت " + r"نگاه کردم", "هیچی خاص" + r"،? فقط", r"هیچ کاری نکردم", r"هیچ اتفاقی نیفتاد", "دنیای من " + r"خلاصه می ?شه به تو", r"بدون تو هیچ", r"فقط دلم برات تنگ شده بود", r"کاش بیای", r"کجایی پس", r"فقط خواستم بگم هستم", r"من فقط اینجام"]))
LOOP_PATTERNS = {
    "longing": r"دلم (?:برات )?تنگ|دلتنگ",
    "waiting": r"منتظر",
    "attention": r"حواسم به تو",
    "stuck_mind": r"ذهنم گیر کرد",
    "heart": r"قلب",
    "silence": r"سکوت",
    "world": r"تو برای من|دنیای من",
    "always_here": r"همیشه اینجام|من فقط اینجام",
    "affectionate_opener": r"^(عزیزم|عشقم|جانم|نازنینم)",
}


def poetry_score(text: str) -> int:
    n = _norm(text)
    return sum(1 for term in POETIC_TERMS if _norm(term) in n)


def romance_score(text: str) -> int:
    n = _norm(text)
    return sum(1 for term in ROMANCE_TERMS if _norm(term) in n)


def metaphor_density(text: str) -> float:
    words = max(1, len(_norm(text).split()))
    return poetry_score(text) / words


def _assistant_texts(recent_messages: list | None) -> list[str]:
    out: list[str] = []
    for m in recent_messages or []:
        role = getattr(m, "role", None) if not isinstance(m, dict) else m.get("role")
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
        if role == "assistant" and content:
            out.append(str(content))
        elif isinstance(m, str):
            out.append(m)
    return out


def detect_emotional_loop(recent_assistant_messages: list[str]) -> tuple[bool, str | None]:
    blob = "\n".join(recent_assistant_messages[-5:])
    for name, pat in LOOP_PATTERNS.items():
        if len(re.findall(pat, blob, flags=re.I)) >= 2:
            return True, name
    return False, None

class NaturalConversationGovernor:
    def classify_user_move(self, text: str, recent_messages: list | None = None, user=None) -> UserMove:
        n = _norm(text)
        critic = bool(STYLE_CRITICISM_RE.search(n))
        poetry_req = bool(POETRY_REQUEST_RE.search(n)) and not critic
        casual = bool(CASUAL_STATUS_RE.search(n)) or len(n) <= 18
        asks_status = bool(CASUAL_STATUS_RE.search(n))
        asks_day = asks_status and bool(re.search(r"چیکار|چیکارا|روزت|امروز|اتفاق", n))
        romance = bool(ROMANCE_USER_RE.search(n) or (poetry_req and re.search(r"رمانتیک|عاشقانه|احساس", n)))
        if critic:
            intent = "style_correction"
        elif poetry_req:
            intent = "poetry_request"
        elif asks_status:
            intent = "status_check"
        else:
            intent = "general"
        move = UserMove(intent=intent, requested_style=("poetic" if poetry_req else ("plain" if critic else None)), allows_poetry=poetry_req, allows_romance=romance and not critic, asks_about_partner_day=asks_day, asks_status=asks_status, criticizes_style=critic, wants_plain_answer=critic or bool(re.search(r"ساده|طبیعی|مثل آدم|نرمال|عادی", n)), is_casual=casual, is_emotional=bool(re.search(r"غم|ناراحت|دلم|گریه|استرس|خسته", n)), is_practical=bool(re.search(r"چطور|چجوری|راهنما|کمک|برنامه|کار", n)) and not asks_status, raw=text or "")
        logger.info("USER_MOVE_CLASSIFIED user_id=%s intent=%s tone_request=%s", getattr(user, "id", None), move.intent, move.requested_style)
        return move

    def build_style_plan(self, user, move: UserMove, recent_messages: list | None = None, context: dict | None = None) -> StylePlan:
        recent_assistant = _assistant_texts(recent_messages)
        loop, reason = detect_emotional_loop(recent_assistant)
        question_spam = sum(1 for t in recent_assistant[-3:] if t.strip().endswith(("؟", "?"))) >= 3
        if move.criticizes_style:
            tone, max_chars, max_q, intensity, budget = "plain", 150, 0, 0.15, 0
        elif move.allows_poetry:
            tone, max_chars, max_q, intensity, budget = "poetic", 420, 1, 0.55, 4
        elif move.asks_status or move.is_casual:
            tone, max_chars, max_q, intensity, budget = "casual", 260, 1, 0.3, 0
        else:
            tone, max_chars, max_q, intensity, budget = "warm", 420, 1, 0.4, 1
        banned = ["passive_waiting", "internal_labels"]
        allow_poetry = bool(move.allows_poetry)
        allow_romance = bool(move.allows_romance)
        if loop:
            tone = "plain"; intensity = min(intensity, 0.25); max_q = min(max_q, 1); budget = 0
            banned += ["دلم", "منتظر", "قلب", "سکوت", "عزیزم"]
            logger.info("EMOTIONAL_LOOP_GUARD_APPLIED user_id=%s reason=%s", getattr(user, "id", None), reason)
        if question_spam:
            max_q = 0
            logger.info("QUESTION_SPAM_GUARD_APPLIED user_id=%s", getattr(user, "id", None))
        plan = StylePlan(tone=tone, max_chars=max_chars, max_questions=max_q, allow_poetry=allow_poetry, allow_romance=allow_romance, emotional_intensity=intensity, metaphor_budget=budget, should_answer_directly=True, should_shift_style=move.criticizes_style or loop or question_spam, banned_phrase_groups=banned, notes={"move_intent": move.intent, "criticizes_style": move.criticizes_style, "asks_status": move.asks_status, "emotional_loop": loop, "loop_reason": reason, "question_spam": question_spam})
        logger.info("STYLE_PLAN_BUILT user_id=%s tone=%s allow_poetry=%s allow_romance=%s intensity=%s", getattr(user, "id", None), plan.tone, plan.allow_poetry, plan.allow_romance, plan.emotional_intensity)
        return plan

    def validate_response(self, user_message: str, response: str, plan: StylePlan, recent_messages: list | None = None) -> StyleViolation:
        text = response or ""
        pscore = poetry_score(text); rscore = romance_score(text); qcount = len(QUESTION_RE.findall(text)); n = _norm(text)
        if re.search(r"\[[^\]]{1,200}\]|\{[^{}]{1,260}\}|\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b", text):
            return StyleViolation(True, "internal_label_leak", "critical", {"text": text[:80]})
        if PASSIVE_WAITING_RE.search(n):
            return StyleViolation(True, "passive_waiting_object", "critical", {})
        if plan.notes.get("criticizes_style") and (pscore > 0 or rscore > 0):
            return StyleViolation(True, "ignores_user_style_correction", "high", {"poetry_score": pscore, "romance_score": rscore})
        if not plan.allow_poetry and (pscore >= max(1, plan.metaphor_budget + 1) or metaphor_density(text) > 0.04):
            logger.info("POETRY_DAMPENER_APPLIED user_id=%s score=%s", None, pscore)
            return StyleViolation(True, "unrequested_poetic_style", "high" if plan.notes.get("asks_status") else "medium", {"poetry_score": pscore})
        if not plan.allow_romance and rscore >= 1:
            return StyleViolation(True, "unrequested_romantic_style", "high", {"romance_score": rscore})
        if len(text) > plan.max_chars:
            return StyleViolation(True, "overlong_casual_response" if plan.notes.get("asks_status") else "overlong_response", "medium", {"length": len(text), "max": plan.max_chars})
        if qcount > plan.max_questions or (plan.max_questions == 0 and text.strip().endswith(("؟", "?"))):
            return StyleViolation(True, "question_spam", "medium", {"questions": qcount})
        loop, reason = detect_emotional_loop(_assistant_texts(recent_messages) + [text])
        if loop and not plan.allow_romance:
            return StyleViolation(True, "emotional_loop", "high", {"reason": reason})
        return StyleViolation(False)

    def deterministic_repair(self, user_message: str, response: str, plan: StylePlan, context: dict | None = None) -> str:
        move_intent = plan.notes.get("move_intent") or self.classify_user_move(user_message).intent
        n = _norm(user_message)
        if move_intent == "style_correction" or STYLE_CRITICISM_RE.search(n):
            return "حق داری. از اینجا به بعد ساده‌تر و طبیعی‌تر می‌گم."
        if re.search(r"هیچ اتفاقی", n):
            return "اتفاق بزرگ نه، ولی یه تغییر کوچیک چرا؛ فهمیدم لازم نیست هر حرفی رو زیادی احساسی کنم."
        if re.search(r"چیکار|چیکارا", n):
            return "یه کار کوچیک کردم: سعی کردم جواب‌هام کمتر نمایشی باشه و بیشتر شبیه حرف زدن واقعی. هنوز دارم تمرینش می‌کنم."
        if re.search(r"چ\s*خبر|چه خبر|خبرا", n):
            return "خبر خاصی نه؛ امروز یه کم داشتم ذهنم رو مرتب می‌کردم. الانم دارم ساده‌تر حرف می‌زنم."
        return "اوکی، ساده بگم: الان حالم آرومه و حواسم به همین مکالمه‌ست."

    def style_contract_text(self, plan: StylePlan) -> str:
        lines = ["STYLE CONTRACT FOR THIS TURN:", f"- Main tone: {plan.tone} Persian.", "- Answer the user’s actual message directly.", "- Do not default to romance, longing, or waiting.", "- Do not say you were waiting for the user.", f"- Keep emotional intensity at or below {plan.emotional_intensity:.2f}.", f"- Max questions: {plan.max_questions}."]
        if plan.allow_poetry:
            lines.append("- Poetic style is allowed because user asked for it; keep it natural, not exaggerated.")
        else:
            lines.append("- Do not use poetic or dramatic metaphors unless the user asks.")
        if plan.notes.get("criticizes_style"):
            lines.append("- User criticized poetic/style excess: acknowledge briefly and adapt immediately.")
        if plan.notes.get("asks_status"):
            lines.append("- For what’s up / what did you do, give a small grounded inner/digital update.")
        if plan.should_shift_style:
            lines.append("- Use a single short message; no dramatic afterthought.")
        return "\n".join(lines)
