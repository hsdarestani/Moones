"""Offline A/B for compact fixed persona versus production persona, same V2 memory.

Never calls Venice or reads real user data. Reports *full prompt characters*,
not provider tokens, final-answer quality or monetary savings.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path

from scripts.evaluate_memory_answers import make_cases


def measure() -> dict:
    previous = {
        "MONES_COMPACT_PERSONA_ENABLED": os.environ.get("MONES_COMPACT_PERSONA_ENABLED"),
        "MONES_COMPACT_PERSONA_SHADOW": os.environ.get("MONES_COMPACT_PERSONA_SHADOW"),
    }
    try:
        os.environ["MONES_COMPACT_PERSONA_ENABLED"] = "false"
        os.environ["MONES_COMPACT_PERSONA_SHADOW"] = "false"
        baseline = make_cases()
        os.environ["MONES_COMPACT_PERSONA_ENABLED"] = "true"
        compact = make_cases()
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    results = []
    for old, new in zip(baseline, compact):
        assert old.id == new.id and old.query == new.query
        assert len(old.legacy_prompt) > 0 and len(new.v2_prompt) > 0
        result = {
            "case_id": old.id,
            "family": old.family,
            "full_legacy_chars": len(old.legacy_prompt),
            "v2_standard_chars": len(old.v2_prompt),
            "v2_compact_chars": len(new.v2_prompt),
            "memory_fact_retained": old.expected is None or (
                (old.expected in old.v2_prompt) == (old.expected in new.v2_prompt)
            ),
        }
        assert result["memory_fact_retained"], f"Evidence dropped from {old.id}"
        results.append(result)

    mean_full = statistics.mean(r["full_legacy_chars"] for r in results)
    mean_v2 = statistics.mean(r["v2_standard_chars"] for r in results)
    mean_compact = statistics.mean(r["v2_compact_chars"] for r in results)
    return {
        "status": "offline_full_prompt_character_measurement",
        "cases": len(results),
        "mean_legacy_chars": round(mean_full),
        "mean_v2_standard_chars": round(mean_v2),
        "mean_v2_compact_chars": round(mean_compact),
        "compact_vs_v2_percent_fewer_chars": round(100 * (1 - mean_compact/mean_v2), 2),
        "compact_vs_legacy_percent_fewer_chars": round(100 * (1 - mean_compact/mean_full), 2),
        "source_facts_present_in_both": all(r["memory_fact_retained"] for r in results),
        "warning": (
            "Measured whole system-prompt CHARACTERS with synthetic inputs only. "
            "Not provider token, billing or generated-answer quality evidence."
        ),
        "details": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="Optional JSON report path")
    args = parser.parse_args()
    data = measure()
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "details"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
