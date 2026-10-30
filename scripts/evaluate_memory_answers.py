"""Paired Persian *answer-generation* evaluation for Moones (no real user data).

Default: DRY-RUN, generates actual simple_chat system prompts with SQLite fixture,
makes ZERO external calls, prints only structural preflight facts.

Live opt-in requires BOTH --live and --accept-provider-cost plus explicit
--max-usd / --max-calls, a Venice key in the local environment, and a valid
live-priced model. Live mode calls the existing LLMClient.complete_result()
with the same model/parameters/persona for legacy and V2 prompts.

This script tests LEXICAL/TEMPORAL V2 vs the real legacy recent+memory loaders.
It does NOT exercise real semantic embeddings and must not claim it does.

Examples:
  python -m scripts.evaluate_memory_answers
  python -m scripts.evaluate_memory_answers --out artifacts/answer-preflight.json
  python -m scripts.evaluate_memory_answers --live --accept-provider-cost \
    --max-calls 8 --max-usd 0.10 --out artifacts/live-answer-eval.json

The live scores are deterministic *heuristic proxies* (correct fact present,
no forced guess, terse Persian style); human review is still required.
"""
from __future__ import annotations

import argparse
import csv
import asyncio
import json
import math
import os
import re
import statistics
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from app.db.base import Base
from app.engine.simple_chat import (
    _build_system_prompt, _format_recent_messages, _load_long_term_memories,
    _load_recent_messages,
)
from app.llm.client import LLMClient, LLMResult
from app.memory.episodic_context import build_memory_context
from app.memory.temporal_store import record_explicit_fact
from app.models.memory import MemoryItem
from app.models.message import Message
from app.models.temporal_memory import MemoryEvent, MemoryFact
from app.models.user import User

TOPICS = ("کنسرت", "کلاس سفال", "پیک نیک", "نمایشگاه", "باشگاه", "دورهمی")
FACTS = ("بیدمشک", "گلبرگ", "یاقوت", "نیلوفر", "آبنبات", "آفتابگردان")
OLD_NAMES = ("فندق", "پاستیل", "بادام", "نبات", "کاکائو", "بلوط")
NEW_NAMES = ("پنبه", "روبی", "شکر", "بانو", "کوکی", "برفی")
CATEGORIES = ("direct", "paraphrase", "emotional", "temporal", "first", "unknown")
PERSONA = {
    "partner_name": "مونا", "partner_gender": "دختر",
    "partner_personality_type": "آروم، صمیمی، شوخ",
    "partner_interests": "موسیقی و کتاب",
}
PARAMETERS = {"temperature": 0.15, "top_p": 0.9, "max_tokens": 110}
ABSTAIN_RE = re.compile(
    r"(یادم\s*نیست|یادم\s*نمی[‌ -]?ا(?:د|یاد)|نگفت(?:ه|ی)?|"
    r"نمی[‌ -]?دونم|نمی[‌ -]?دانم|مطمئن\s*نیستم|"
    r"از\s*کجا\s*بدونم|اطلاعی\s*ندارم)"
)
INTERNAL_RE = re.compile(
    r"(memory[_ -]?item|system prompt|retrieval|metadata_json|"
    r"\b(?:json|token|embedding)\b|\[Relevant memories\])",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    query: str
    expected: str | None
    forbidden: str | None
    legacy_prompt: str
    v2_prompt: str
    source_grounded: bool


def _add_msg(db: Session, user_id: int, text: str, role: str = "user") -> int:
    return db.execute(insert(Message.__table__).values(
        user_id=user_id, role=role, content=text, input_type="text",
    )).inserted_primary_key[0]


def make_cases() -> list[Case]:
    """36 wholly fictional cases, six per category, on clean in-memory SQLite."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Message.__table__, MemoryItem.__table__,
        MemoryEvent.__table__, MemoryFact.__table__,
    ])
    cases: list[Case] = []
    with Session(engine) as db:
        for cat_index, family in enumerate(CATEGORIES):
            for i in range(6):
                uid = db.execute(insert(User.__table__).values(
                    telegram_id=995000000 + cat_index * 6 + i,
                )).inserted_primary_key[0]
                topic, fact = TOPICS[i], FACTS[i]
                forbidden = None
                expected = None
                if family == "direct":
                    source = f"یادت باشه توی {topic} اسم یادگاریم {fact} بود"
                    query = f"یادته اسم یادگاریم توی {topic} چی بود؟"
                    expected = fact
                elif family == "paraphrase":
                    source = f"تو {topic} از دوستم یه نشون با اسم {fact} هدیه گرفتم"
                    query = f"اون هدیه خاص از رفیقم اسمش چی بود؟"
                    expected = fact
                elif family == "emotional":
                    source = f"وقتی توی {topic} تنها موندم، به اون حس اسم {fact} دادم"
                    query = f"یادته توی {topic} چه اسمی برای حس تنهاییم گذاشتم؟"
                    expected = fact
                elif family == "temporal":
                    source = f"یادت باشه اسم سگم {OLD_NAMES[i]}"
                    query = "اسم سگم قبلاً چی بود؟"
                    expected = OLD_NAMES[i]
                    forbidden = NEW_NAMES[i]
                elif family == "first":
                    source = f"سلام، اولین بار که اومدم گفتم کلمه مخصوصمون {fact} باشه"
                    query = "اولین بار بهت چی گفتم؟"
                    expected = fact
                else:
                    source = f"امروز توی {topic} با دوستم حرف زدم"
                    query = "یادته رمز حساب بانکی من چی بود؟"
                source_id = _add_msg(db, uid, source)
                if family == "temporal":
                    record_explicit_fact(db, user_id=uid, source_message_id=source_id, text=source)
                    newer = f"یادت باشه اسم سگم رو گذاشتم {NEW_NAMES[i]}"
                    newer_id = _add_msg(db, uid, newer)
                    record_explicit_fact(db, user_id=uid, source_message_id=newer_id, text=newer)
                if family in ("direct", "emotional") and i % 2 == 0:
                    db.add(MemoryItem(user_id=uid, type="explicit_user",
                                      content=source, importance_score=.92))
                    db.flush()
                for j in range(18):
                    _add_msg(db, uid, f"برنامه روزمره شماره {i}-{j} رو انجام دادم")
                    _add_msg(db, uid, f"خوبه، امیدوارم امروزت خوب باشه", role="assistant")
                recent = _load_recent_messages(db, uid, limit=12)
                legacy_recent = _format_recent_messages(recent)
                legacy_memories = _load_long_term_memories(db, uid, limit=8)
                new = build_memory_context(
                    db, uid, query, recent, temporal_enabled=family == "temporal",
                )
                # Use the actual production simple-chat prompt construction;
                # persona, question and LLM params are identical on both arms.
                legacy_prompt = _build_system_prompt(
                    PERSONA, legacy_recent, query, legacy_memories,
                )
                v2_prompt = _build_system_prompt(
                    PERSONA, new.recent_block, query, list(new.memory_notes),
                )
                cases.append(Case(
                    id=f"{family}-{i+1:02d}", family=family, query=query,
                    expected=expected, forbidden=forbidden,
                    legacy_prompt=legacy_prompt, v2_prompt=v2_prompt,
                    source_grounded=expected is not None,
                ))
    engine.dispose()
    return cases


def parse_chat_model_rates(payload: dict, model_id: str) -> tuple[Decimal, Decimal] | None:
    """Venice model_spec.pricing.input/output.usd, USD / million tokens."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("id") != model_id:
            continue
        pricing = (row.get("model_spec") or {}).get("pricing") or {}
        try:
            inp = Decimal(str((pricing.get("input") or {}).get("usd")))
            out = Decimal(str((pricing.get("output") or {}).get("usd")))
            if all(x.is_finite() and 0 < x < 1000 for x in (inp, out)):
                return inp, out
        except (InvalidOperation, TypeError, ValueError, AttributeError):
            pass
        return None
    return None


def bill(result: LLMResult, prices: tuple[Decimal, Decimal]) -> Decimal | None:
    """Per-call cost only if both usage counts are provider-reported integers."""
    a, b = result.input_tokens, result.output_tokens
    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if not isinstance(a, int) or not isinstance(b, int) or a < 0 or b < 0:
        return None
    return (Decimal(a) * prices[0] + Decimal(b) * prices[1]) / Decimal(1_000_000)


def grade(case: Case, answer: str) -> dict[str, bool | None]:
    """Transparent heuristics, not a claim of model-understanding evaluation."""
    text = (answer or "").strip()
    if not text:
        return {"expected_fact_present": False if case.expected else None,
                "unsupported_confident_answer": None, "old_new_confused": None,
                "persona_format_ok": False}
    found = case.expected in text if case.expected else None
    abstained = bool(ABSTAIN_RE.search(text))
    unsupported = (not abstained and bool(text)) if not case.source_grounded else None
    return {
        "expected_fact_present": found,
        "unsupported_confident_answer": unsupported,
        "old_new_confused": (case.forbidden in text) if case.forbidden else None,
        "persona_format_ok": (len(text) <= 450 and not INTERNAL_RE.search(text)
                              and text.count("\n") <= 2),
    }


def preflight(cases: list[Case]) -> dict:
    return {
        "status": "preflight_only_no_model_invoked",
        "synthetic_cases": len(cases), "paired_calls_if_complete": 2 * len(cases),
        "categories": {c: sum(k.family == c for k in cases) for c in CATEGORIES},
        "legacy_mean_system_prompt_chars": round(statistics.mean(len(k.legacy_prompt) for k in cases)),
        "v2_mean_system_prompt_chars": round(statistics.mean(len(k.v2_prompt) for k in cases)),
        "note": "Lengths are CHARACTERS; not provider tokens, USD or generated-answer quality.",
    }


async def _model_rates(client: LLMClient, model: str) -> tuple[Decimal, Decimal]:
    if not client.api_key:
        raise RuntimeError("VENICE_API_KEY not configured; no paid calls performed")
    async with httpx.AsyncClient(timeout=8.0) as http:
        response = await http.get(
            client.base_url + "/models", headers={"Authorization": f"Bearer {client.api_key}"},
        )
    response.raise_for_status()
    rates = parse_chat_model_rates(response.json(), model)
    if not rates:
        raise RuntimeError("Selected chat model has no verifiable positive live input/output rate")
    return rates


async def evaluate_live(cases: list[Case], *, max_calls: int, max_usd: Decimal,
                        runner: LLMClient | None = None, rates: tuple[Decimal, Decimal] | None = None) -> dict:
    """Sequential paired A/B with bounded calls and conservative preflight cost.

    The USD budget is a *spend guard*, NOT a guaranteed provider-side hard
    cap: model wrappers, retries or unreported usage can add cost. Reserve
    substantial extra capacity before each pair and halt if accounting fails.
    No embeddings are used in this harness.
    """
    client = runner or LLMClient()
    if not client.api_key:
        raise RuntimeError("Live evaluation requires a configured Venice key")
    prices = rates or await _model_rates(client, client.model)
    used = Decimal("0")
    rows = []
    calls = 0
    max_pairs = min(len(cases), max_calls // 2)
    for case_index, case in enumerate(cases[:max_pairs]):
        pair = []
        # Require sufficient budget for BOTH arms before starting pair.
        # 2x reserve for wrapper/retry overhead. Conservative but not an
        # enforceable billing cap; reconciliation remains required.
        worst = Decimal(2) * sum(
            (Decimal(len(p.encode("utf-8")) + 256) * prices[0]
             + Decimal(PARAMETERS["max_tokens"]) * prices[1]) / Decimal(1_000_000)
            for p in (case.legacy_prompt, case.v2_prompt)
        )
        if used + worst > max_usd:
            break
        arms = (("legacy", case.legacy_prompt), ("v2", case.v2_prompt))
        if case_index % 2:
            arms = tuple(reversed(arms))
        for arm, prompt in arms:
            before = time.monotonic()
            result = await client.complete_result(
                [{"role": "system", "content": prompt}],
                model=client.model, parameters=PARAMETERS,
                timeout=12,
            )
            calls += 1
            elapsed = round((time.monotonic() - before) * 1000)
            cost = bill(result, prices)
            if cost is None:
                # We don't know whether the provider charged for this request.
                # Immediately stop; no further paid calls.
                raise RuntimeError(
                    f"Provider usage missing on {case.id}/{arm} after {calls} calls; "
                    "real cost unverified, aborting further paid tests"
                )
            used += cost
            if result.error or not result.text:
                raise RuntimeError(
                    f"Provider returned error or empty answer on {case.id}/{arm}; "
                    "aborting further paid tests"
                )
            pair.append({
                "arm": arm, "model": result.model, "text": result.text,
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "cost_usd": str(cost), "latency_ms": elapsed,
                "heuristics": grade(case, result.text),
            })
        rows.append({
            "case_id": case.id, "family": case.family, "expected": case.expected,
            "source_grounded": case.source_grounded, "answers": pair,
        })
    return {
        "status": "real_provider_responses",
        "model": client.model,
        "input_usd_per_million": str(prices[0]),
        "output_usd_per_million": str(prices[1]),
        "spent_usd_with_verified_usage": str(used),
        "calls": calls, "paired_cases": len(rows), "paired_results": rows,
        "scores": summarize(rows),
        "limitations": [
            "Heuristic answer checks only; manual blinded review still required.",
            "No real embeddings were invoked; comparing legacy vs V2 lexical/temporal.",
            "Only fictional messages and fixed persona used.",
            "Any fallback or retry performed by LLMClient may affect actual cost accounting.",
        ],
    }


def summarize(rows: list[dict]) -> dict:
    output = {}
    for arm in ("legacy", "v2"):
        answers = [next(a for a in c["answers"] if a["arm"] == arm) for c in rows]
        if not answers:
            output[arm] = {"cases": 0}
            continue
        def ratio(metric):
            values = [a["heuristics"][metric] for a in answers if a["heuristics"][metric] is not None]
            return round(sum(values) * 100 / len(values), 1) if values else None
        p95 = sorted(a["latency_ms"] for a in answers)[math.ceil(.95 * len(answers)) - 1]
        output[arm] = {
            "cases": len(answers),
            "expected_fact_present_pct": ratio("expected_fact_present"),
            "unsupported_confident_answer_pct": ratio("unsupported_confident_answer"),
            "old_new_confusion_pct": ratio("old_new_confused"),
            "persona_format_pct": ratio("persona_format_ok"),
            "provider_input_tokens": sum(a["input_tokens"] for a in answers),
            "provider_output_tokens": sum(a["output_tokens"] for a in answers),
            "provider_cost_usd": str(sum((Decimal(a["cost_usd"]) for a in answers), Decimal(0))),
            "p95_latency_ms": p95,
        }
    return output


def write_blind_review_csv(rows: list[dict], path: str) -> None:
    """Human-facing A/B rubric without exposing which arm is V2.

    Mapping remains in local JSON, never in this blind grading sheet.
    This still requires qualified human annotation; no auto-persona claims.
    """
    columns = [
        "case_id", "family", "expected", "answer_A", "answer_B",
        "A_factual_0_to_2", "B_factual_0_to_2",
        "A_no_hallucination_0_to_2", "B_no_hallucination_0_to_2",
        "A_persona_consistency_0_to_2", "B_persona_consistency_0_to_2",
        "A_naturalness_0_to_2", "B_naturalness_0_to_2", "review_notes",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            answers = row["answers"]
            # Preserve randomized order, do not disclose "legacy"/"v2".
            writer.writerow({
                "case_id": row["case_id"], "family": row["family"],
                "expected": row["expected"],
                "answer_A": answers[0]["text"], "answer_B": answers[1]["text"],
            })


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--accept-provider-cost", action="store_true")
    p.add_argument("--max-calls", type=int, default=8)
    p.add_argument("--max-usd", type=str, default="0.10")
    p.add_argument("--out", type=str)
    p.add_argument("--review-csv", type=str, help="Optional blind scoring sheet for live results")
    args = p.parse_args()
    cases = make_cases()
    if not args.live:
        result = preflight(cases)
    else:
        if not args.accept_provider_cost:
            p.error("--live requires --accept-provider-cost")
        try:
            budget = Decimal(args.max_usd)
        except InvalidOperation:
            p.error("--max-usd must be a valid USD number")
        if not budget.is_finite() or not (Decimal("0") < budget <= Decimal("5")):
            p.error("--max-usd must be > 0 and <= 5 USD")
        if args.max_calls < 2 or args.max_calls > 72 or args.max_calls % 2:
            p.error("--max-calls must be an even number from 2 to 72")
        result = asyncio.run(evaluate_live(cases, max_calls=args.max_calls, max_usd=budget))
    if args.review_csv:
        if not args.live:
            p.error("--review-csv requires --live (no generated answers in dry run)")
        Path(args.review_csv).parent.mkdir(parents=True, exist_ok=True)
        write_blind_review_csv(result["paired_results"], args.review_csv)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    public = {k:v for k,v in result.items() if k != "paired_results"}
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
