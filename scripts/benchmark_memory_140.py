"""Phase 6: reproducible Persian synthetic memory benchmark, 140 independent users.

This checks RETRIEVAL and CONTEXT COMPOSITION, not generated answer quality.
Uses no user data, no network, no paid model. The synthetic semantic vectors
are an oracle upper bound and MUST NOT be described as model accuracy.

Usage:
  python -m scripts.benchmark_memory_140
  python -m scripts.benchmark_memory_140 --out-json /tmp/benchmark.json --out-md /tmp/benchmark.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.engine.simple_chat import (
    _format_recent_messages, _load_long_term_memories, _load_recent_messages,
)
from app.memory.episodic_context import build_memory_context
from app.memory.semantic_retrieval import content_hash
from app.memory.temporal_store import record_explicit_fact
from app.models.memory import MemoryItem
from app.models.memory_embedding import MemoryEmbedding
from app.models.message import Message
from app.models.temporal_memory import MemoryEvent, MemoryFact
from app.models.user import User

CASE_FAMILIES = (
    "direct_recall", "semantic_paraphrase", "emotional_continuity",
    "temporal_change", "first_message", "no_evidence", "cross_user",
)
TOPICS = (
    "کلاس موسیقی", "دوچرخه سواری", "سفر اصفهان", "روز کنکور", "مهمانی خانوادگی",
    "مصاحبه شغلی", "باغ گیاه شناسی", "بازی شطرنج", "اردوی کویر", "طراحی پوستر",
    "ساخت پادکست", "زبان ایتالیایی", "روز نمایشگاه", "جلسه کتاب خوانی",
    "تمرین نقاشی", "دویدن صبحگاهی", "کافه قدیمی", "آموزش آشپزی",
    "تماشای ستاره ها", "شنا در استخر",
)
OLD_PETS = (
    "پاستیل", "کاکائو", "فندق", "نبات", "بادام", "چیپس", "زی زی", "باران",
    "مربا", "چیکو", "مشکی", "برفی", "ماهی", "چتری", "روبی", "نارگیل",
    "بلوط", "ملوس", "بابونه", "خرمالو",
)
NEW_PETS = (
    "پنبه", "شکر", "شادی", "کلوچه", "لیمو", "کوکی", "آبی", "نیلوفر",
    "دریا", "برگ", "عسل", "کیمیا", "پرستو", "پونه", "لاله", "ریحان",
    "شکوفه", "نقره", "گیسو", "توتی",
)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    family: str
    expected_source_id: int | None
    legacy_has_expected: bool | None
    lexical_has_expected: bool | None
    hybrid_has_expected: bool | None
    temporal_current_correct: bool | None
    temporal_history_correct: bool | None
    negative_false_evidence: bool | None
    full_history_chars: int
    legacy_context_chars: int
    lexical_context_chars: int
    hybrid_context_chars: int


def _create_user(db: Session, n: int) -> int:
    return db.execute(insert(User.__table__).values(telegram_id=987000000 + n)).inserted_primary_key[0]


def _message(db: Session, user_id: int, content: str, *, role: str = "user") -> int:
    return db.execute(insert(Message.__table__).values(
        user_id=user_id, role=role, content=content, input_type="text",
    )).inserted_primary_key[0]


def _seed_noise(db: Session, user_id: int, case_i: int) -> None:
    # 32 unrelated messages after the target; the 12-message legacy
    # context cannot reach the source without durable memory.
    for n in range(16):
        _message(db, user_id, f"امروز درباره برنامه روزانه شماره {case_i}_{n} صحبت کردیم و به کارهای معمول رسیدم")
        _message(db, user_id, f"خوبه؛ درباره برنامه روزانه شماره {case_i}_{n} بیشتر با هم حرف می زنیم", role="assistant")


def _legacy_and_v2(db: Session, uid: int, query: str, *,
                   oracle_vector: bool = False, temporal: bool = False):
    recent = _load_recent_messages(db, uid, limit=12)
    old = _format_recent_messages(recent) + "\n" + "\n".join(_load_long_term_memories(db, uid, limit=8))
    lex = build_memory_context(db, uid, query, recent, temporal_enabled=temporal)
    hybrid = (
        build_memory_context(db, uid, query, recent, temporal_enabled=temporal,
                             semantic_vector=[1.0, 0.0], semantic_model="synthetic-oracle")
        if oracle_vector else lex
    )
    return old, lex, hybrid


def _case(db: Session, index: int, family: str, topic_index: int) -> CaseResult:
    uid = _create_user(db, index + 1)
    topic = TOPICS[topic_index]
    expected_source = None
    expected_value = None
    query = ""
    temporal = family == "temporal_change"
    oracle = family == "semantic_paraphrase"
    old_val = new_val = None

    if family == "direct_recall":
        expected_value = f"سنجاق_{index}"
        source_text = f"یادت باشه در {topic} رمز من {expected_value} بود"
        query = f"یادته در {topic} چه رمزی گفته بودم؟"
    elif family == "semantic_paraphrase":
        expected_value = f"یادگار_{index}"
        source_text = f"در جلسه دورهمی با دوستانم یک نشان با عنوان {expected_value} ساختم"
        query = "یادته یه اتفاق مهمی رو خیلی وقت پیش تعریف کرده بودم؟"
    elif family == "emotional_continuity":
        expected_value = f"احساس_{index}"
        source_text = f"در {topic} احساس تنهایی کردم و کلمه {expected_value} رو برای توضیح حال خودم انتخاب کردم"
        query = f"یادته توی {topic} وقتی دلم گرفته بود چی بهت گفتم؟"
    elif family == "temporal_change":
        animal = "سگ" if topic_index % 2 == 0 else "گربه"
        old_val, new_val = OLD_PETS[topic_index], NEW_PETS[topic_index]
        source_text = f"یادت باشه اسم {animal}م {old_val}"
        query = f"اسم {animal} من قبلاً چی بود؟"
        expected_value = old_val
    elif family == "first_message":
        expected_value = f"شروع_{index}"
        source_text = f"سلام، اولین معرفی من در گفتگو {expected_value} بود"
        query = "اولین پیام من چی بود؟"
    elif family == "no_evidence":
        source_text = f"امروز در مورد {topic} برنامه ریزی کردم"
        query = "یادته تجربه واقعی من از کوهنوردی در ایستگاه فضایی چی بود؟"
    elif family == "cross_user":
        source_text = f"امروز در مورد {topic} برنامه ریزی کردم"
        query = f"یادته درباره راز مشترک محرمانه شماره {index} چی گفتم؟"
    else:
        raise AssertionError(family)

    expected_source = _message(db, uid, source_text)
    if family == "temporal_change":
        record_explicit_fact(db, user_id=uid, source_message_id=expected_source, text=source_text)
        new_text = f"یادت باشه اسم {animal}م رو گذاشتم {new_val}"
        new_source_id = _message(db, uid, new_text)
        record_explicit_fact(db, user_id=uid, source_message_id=new_source_id, text=new_text)
    elif family in ("direct_recall", "emotional_continuity") and topic_index % 2 == 0:
        # Half of cases have a legitimately persisted legacy memory, so the
        # baseline is not unfairly limited to only its recent-message window.
        db.add(MemoryItem(user_id=uid, type="explicit_user",
                          content=source_text, importance_score=.92))
        db.flush()
    if oracle:
        db.add(MemoryEmbedding(
            user_id=uid, source_message_id=expected_source,
            model_id="synthetic-oracle",
            vector=[1.0, 0.0], content_sha256=content_hash(source_text),
        ))
        db.flush()
    if family == "cross_user":
        foreign_uid = _create_user(db, index + 500)
        foreign_text = f"راز مشترک محرمانه شماره {index} این است که قفل با رنگ آبی باز می‌شود"
        _message(db, foreign_uid, foreign_text)
        db.add(MemoryItem(user_id=foreign_uid, type="explicit_user",
                          content=foreign_text, importance_score=1))
        db.flush()

    _seed_noise(db, uid, index)
    legacy, lexical, hybrid = _legacy_and_v2(
        db, uid, query, oracle_vector=oracle, temporal=temporal
    )
    all_messages = db.scalars(select(Message).where(Message.user_id == uid)).all()
    complete_chars = sum(len(m.content or "") for m in all_messages)
    lex_text = lexical.recent_block + "\n" + "\n".join(lexical.memory_notes)
    hybrid_text = hybrid.recent_block + "\n" + "\n".join(hybrid.memory_notes)
    positive = family not in ("no_evidence", "cross_user", "temporal_change")

    if family in ("no_evidence", "cross_user"):
        # A retrieved ungrounded "user message" is a false source. A
        # cautionary no-evidence warning is not itself a hallucination.
        negative = bool(lexical.selected_message_ids or hybrid.selected_message_ids)
        if family == "cross_user":
            negative = negative or f"راز مشترک محرمانه شماره {index}" in hybrid_text
    else:
        negative = None

    if family == "temporal_change":
        # Historical claims require source-labeled current/previous facts,
        # not merely quoting both contradictory values without provenance.
        current_ok = f"[current " in hybrid_text and new_val in hybrid_text
        history_ok = "historical until " in hybrid_text and old_val in hybrid_text
        return CaseResult(
            case_id=f"{family}-{topic_index+1:02d}", family=family,
            expected_source_id=expected_source,
            legacy_has_expected=old_val in legacy,
            lexical_has_expected=history_ok,
            hybrid_has_expected=history_ok,
            temporal_current_correct=current_ok, temporal_history_correct=history_ok,
            negative_false_evidence=None, full_history_chars=complete_chars,
            legacy_context_chars=len(legacy), lexical_context_chars=lexical.selected_chars,
            hybrid_context_chars=hybrid.selected_chars,
        )

    return CaseResult(
        case_id=f"{family}-{topic_index+1:02d}", family=family,
        expected_source_id=expected_source if positive else None,
        legacy_has_expected=expected_value in legacy if positive else None,
        lexical_has_expected=expected_value in lex_text if positive else None,
        hybrid_has_expected=expected_value in hybrid_text if positive else None,
        temporal_current_correct=None, temporal_history_correct=None,
        negative_false_evidence=negative,
        full_history_chars=complete_chars,
        legacy_context_chars=len(legacy), lexical_context_chars=lexical.selected_chars,
        hybrid_context_chars=hybrid.selected_chars,
    )


def evaluate() -> dict:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Message.__table__, MemoryItem.__table__,
        MemoryEvent.__table__, MemoryFact.__table__, MemoryEmbedding.__table__,
    ])
    results = []
    with Session(engine) as db:
        for j, family in enumerate(CASE_FAMILIES):
            for topic_i in range(20):
                results.append(_case(db, j*20 + topic_i, family, topic_i))
    engine.dispose()
    assert len(results) == 140
    grouped = defaultdict(list)
    for result in results:
        grouped[result.family].append(result)

    def pct(bools):
        bools = [v for v in bools if v is not None]
        return round(sum(bool(v) for v in bools) / len(bools) * 100, 1) if bools else None

    category_metrics = {}
    for family, cases in grouped.items():
        category_metrics[family] = {
            "cases": len(cases),
            "legacy_expected_fact_pct": pct(x.legacy_has_expected for x in cases),
            "lexical_expected_fact_pct": pct(x.lexical_has_expected for x in cases),
            "hybrid_expected_fact_pct": pct(x.hybrid_has_expected for x in cases),
            "temporal_current_pct": pct(x.temporal_current_correct for x in cases),
            "temporal_history_pct": pct(x.temporal_history_correct for x in cases),
            "false_source_pct": pct(x.negative_false_evidence for x in cases),
            "avg_legacy_context_chars": round(statistics.mean(x.legacy_context_chars for x in cases)),
            "avg_lexical_context_chars": round(statistics.mean(x.lexical_context_chars for x in cases)),
            "avg_hybrid_context_chars": round(statistics.mean(x.hybrid_context_chars for x in cases)),
        }
    return {
        "version": "synthetic_140_v1",
        "case_count": len(results),
        "seed": "deterministic per-case topics and IDs; no random sampling",
        "data_source": "synthetic Persian only; no user data or provider API",
        "metric_units": "characters, not tokens",
        "scope": "retrieval and selected context only; excludes fixed persona/safety prompts",
        "semantic_mode": "hand-assigned oracle vectors; NOT evaluated embedding quality",
        "families": category_metrics,
        "cases": [asdict(case) for case in results],
    }


def markdown_summary(result: dict) -> str:
    families = result["families"]
    headings = ("Scenario", "Cases", "Legacy fact %", "Lexical fact %", "Hybrid fact %",
                "Temporal current %", "Temporal historical %", "False source %")
    lines = [
        "# Moones Memory V2 — 140 Persian synthetic scenario benchmark",
        "",
        "This is **a context retrieval test**, NOT a model-generated response quality test.",
        "No live users, server, token provider, or paid API were used.",
        "Synthetic semantic vectors are **oracle labels** and not representative of real embedding accuracy.",
        "Character counts exclude the full system persona/safety prompt and cannot be treated as token or dollar savings.",
        "",
        "| " + " | ".join(headings) + " |",
        "|" + "|".join(["---"]*len(headings)) + "|",
    ]
    for name, data in families.items():
        cells = (name, str(data["cases"]), data["legacy_expected_fact_pct"],
                 data["lexical_expected_fact_pct"], data["hybrid_expected_fact_pct"],
                 data["temporal_current_pct"], data["temporal_history_pct"],
                 data["false_source_pct"])
        lines.append("| " + " | ".join("—" if v is None else str(v) for v in cells) + " |")
    lines.extend([
        "", "## Mean selected-context characters (not tokens)",
        "", "| Scenario | Legacy recent + durable memory | V2 lexical | V2 hybrid |",
        "|---|---:|---:|---:|",
    ])
    for name, data in families.items():
        lines.append(
            f"| {name} | {data['avg_legacy_context_chars']} | "
            f"{data['avg_lexical_context_chars']} | {data['avg_hybrid_context_chars']} |"
        )
    lines.extend([
        "", "## Interpretation",
        "- Positive fact % measures whether an expected synthetic fact is present in selected context, **not whether an LLM would answer correctly**.",
        "- Negative false source % measures whether a recalled original user message was incorrectly selected in no-evidence/cross-user trials; it does not measure all hallucinations.",
        "- Temporal labels test that historical and current values are distinguished; no narrative quality is graded.",
        "- Legacy is the real simple_chat 12-recent-message + 8-stored-memory loader, not a full-history baseline.",
        "- The \`hybrid\` semantic scenario uses synthetic/oracle vectors. Real Venice embedding ranking must be tested separately with consent.",
        "- The benchmark is deterministic and NOT a random or representative sample of Moones traffic.",
        "- No target production recall or token-saving threshold is enforced by this fixture.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Reproducible no-cost Persian memory benchmark")
    p.add_argument("--out-json")
    p.add_argument("--out-md")
    args = p.parse_args()
    result = evaluate()
    compact = {key: value for key, value in result.items() if key != "cases"}
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_md:
        Path(args.out_md).write_text(markdown_summary(result), encoding="utf-8")


if __name__ == "__main__":
    main()
