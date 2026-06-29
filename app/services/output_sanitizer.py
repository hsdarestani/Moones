from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LABEL_MAP = {
    "business_work": "کار و مسیر حرفه‌ای",
    "beauty_cosmetics": "زیبایی و مراقبت از خود",
    "romance": "حال‌وهوای عاشقانه",
    "music": "موسیقی",
    "family": "خانواده",
    "health": "سلامتی",
    "study": "یادگیری",
    "money": "مسائل مالی",
    "life_update": "",
    "inner_reflection": "",
    "memory_callback": "",
    "playful_ping": "",
    "romantic_note": "",
    "caring_note": "",
    "activity_invite": "",
    "soft_upsell": "",
    "simple_checkin": "",
}
INTERNAL_TERMS = (
    "intent", "metadata", "category", "relationship_stage", "partner_profile",
    "memory_key", "selected_memories", "system prompt", "prompt", "debug",
    "reasoning_content", "assistant_debug",
)
SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b")
LIST_RE = re.compile(r"\[(?:[^\[\]]{0,160})\]")
OBJECT_RE = re.compile(r"\{(?:[^{}]{0,220})\}")
META_ASSIGN_RE = re.compile(r"\b(?:intent|metadata|category|relationship_stage|partner_profile|memory_key|selected_memories)\s*[:=]\s*[^،.\n]+", re.I)

@dataclass
class SanitizerResult:
    text: str
    changed: bool
    reason: str | None = None


def sanitize_output(text: str, user_id: int | None = None) -> SanitizerResult:
    original = text or ""
    out = original.strip()
    reasons: list[str] = []

    if LIST_RE.search(out):
        reasons.append("json_array")
        # Replace known single labels in arrays with natural phrase, otherwise remove artifact.
        def repl_list(match: re.Match[str]) -> str:
            blob = match.group(0)
            labels = SNAKE_RE.findall(blob)
            natural = [LABEL_MAP[l] for l in labels if LABEL_MAP.get(l)]
            return "، ".join(dict.fromkeys(natural)) if natural else ""
        out = LIST_RE.sub(repl_list, out)

    if OBJECT_RE.search(out) and any(term in out.lower() for term in INTERNAL_TERMS):
        reasons.append("json_object")
        out = OBJECT_RE.sub("", out)

    if META_ASSIGN_RE.search(out):
        reasons.append("metadata_assignment")
        out = META_ASSIGN_RE.sub("", out)

    def repl_snake(match: re.Match[str]) -> str:
        label = match.group(0)
        if label in LABEL_MAP:
            reasons.append("known_label")
            return LABEL_MAP[label]
        reasons.append("snake_case")
        return ""
    out = SNAKE_RE.sub(repl_snake, out)

    for term in INTERNAL_TERMS:
        if term.lower() in out.lower():
            reasons.append("internal_term")
            out = re.sub(re.escape(term), "", out, flags=re.I)

    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([،.!؟?])", r"\1", out)
    out = re.sub(r"(?:حال‌وهوای|حس و حال)\s*(?:که|\.)", "", out)
    out = out.strip(" -،؛\n\t")
    changed = out != original.strip()
    if changed:
        logger.info("OUTPUT_SANITIZED user_id=%s reason=internal_label_leak", user_id)
    return SanitizerResult(out or "چیز خاصی نه.", changed, ",".join(sorted(set(reasons))) if reasons else None)
