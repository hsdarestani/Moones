from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACTIONS = ["chat", "generate_new", "refine_previous", "variation", "resend_exact", "clarify"]

@dataclass
class ActionMetrics:
    precision: float
    recall: float
    f1: float
    support: int


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_predictions(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_action = {}
    counts = {a: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for a in ACTIONS}
    total_constraints = retained_constraints = 0
    wrong_source = 0
    resend_total = resend_ok = refinement_total = refinement_ok = 0
    for case, pred in zip(cases, predictions, strict=True):
        exp = case["expected_action"]
        got = pred["action"]
        counts[exp]["support"] += 1
        for a in ACTIONS:
            if got == a and exp == a: counts[a]["tp"] += 1
            elif got == a and exp != a: counts[a]["fp"] += 1
            elif got != a and exp == a: counts[a]["fn"] += 1
        exp_constraints = set(case.get("expected_visual_constraints", {}).keys())
        got_constraints = set((pred.get("visual_intent") or {}).keys())
        total_constraints += len(exp_constraints)
        retained_constraints += len(exp_constraints & got_constraints)
        if exp == "refine_previous":
            refinement_total += 1
            refinement_ok += int(bool(pred.get("source_reference_valid", True)))
        if exp == "resend_exact":
            resend_total += 1
            resend_ok += int(got == "resend_exact" and bool(pred.get("source_reference_valid", True)))
        if exp in {"refine_previous", "variation", "resend_exact"} and pred.get("wrong_old_source"):
            wrong_source += 1
    for a, c in counts.items():
        p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
        r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
        f = 2*p*r/(p+r) if p+r else 0.0
        by_action[a] = ActionMetrics(round(p, 4), round(r, 4), round(f, 4), c["support"]).__dict__
    ordinary_chat = [c for c in cases if c["expected_action"] == "chat"]
    genuine = [c for c in cases if c["expected_media_delivery_requested"]]
    return {
        "by_action": by_action,
        "false_image_generation_rate": _rate(cases, predictions, lambda c,p: c["expected_action"] == "chat" and p["action"] in {"generate_new","refine_previous","variation"}),
        "missed_image_request_rate": _rate(cases, predictions, lambda c,p: c["expected_media_delivery_requested"] and not p.get("media_delivery_requested")),
        "unnecessary_clarification_rate": _rate(cases, predictions, lambda c,p: c["expected_action"] != "clarify" and p["action"] == "clarify"),
        "refinement_source_resolution_accuracy": round(refinement_ok / refinement_total, 4) if refinement_total else 0,
        "resend_accuracy": round(resend_ok / resend_total, 4) if resend_total else 0,
        "semantic_constraint_retention_rate": round(retained_constraints / total_constraints, 4) if total_constraints else 1,
        "wrong_attachment_to_old_image_count": wrong_source,
        "billing_before_confirmed_image_intent_count": sum(1 for p in predictions if p.get("reserved_billing_before_clarification")),
        "ordinary_chat_support": len(ordinary_chat),
        "genuine_visual_request_support": len(genuine),
    }


def _rate(cases, preds, fn):
    denom = len(cases) or 1
    return round(sum(1 for c, p in zip(cases, preds, strict=True) if fn(c, p)) / denom, 4)
