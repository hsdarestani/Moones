from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:220]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "conftest.py",
    "import hashlib\n",
    "import hashlib\nimport json\n",
)

replace_once(
    "conftest.py",
    '''    from app.llm.image_client import VENICE_SEED_MIN, VeniceImageClient
    from app.services.generated_image_qa_service import (
        corrective_prompt_for_reasons,
        evaluate_adult_anatomy_image,
        evaluate_single_subject_image,
    )
''',
    '''    from app.llm.image_client import VENICE_SEED_MIN, VeniceImageClient
    from app.llm.vision_client import analyze_image_bytes_with_venice
    from app.services.generated_image_qa_service import (
        ADULT_ANATOMY_PROFILE_QA_PROMPT,
        ADULT_ANATOMY_QA_SCHEMA,
        ADULT_ANATOMY_STRUCTURE_QA_PROMPT,
        corrective_prompt_for_reasons,
        evaluate_adult_anatomy_image,
        evaluate_single_subject_image,
    )
''',
)

replace_once(
    "conftest.py",
    '''    model = "krea-2-turbo"
    client = VeniceImageClient()
''',
    '''    async def diagnose_anatomy_reviewers(image_bytes: bytes, anatomical_profile: str) -> list[dict]:
        fallback = settings.vision_fallback_model or settings.vision_model
        review_plan = [
            (settings.vision_model, ADULT_ANATOMY_PROFILE_QA_PROMPT, "profile"),
            (fallback, ADULT_ANATOMY_STRUCTURE_QA_PROMPT, "structure"),
        ]
        summaries: list[dict] = []
        for review_model, review_prompt, phase in review_plan:
            prompt = (
                review_prompt
                + "\\nSchema: "
                + ADULT_ANATOMY_QA_SCHEMA
                + "\\nRequirements: "
                + json.dumps({"anatomical_profile": anatomical_profile}, sort_keys=True)
            )
            try:
                payload = await analyze_image_bytes_with_venice(
                    image_bytes,
                    prompt=prompt,
                    model=review_model,
                )
            except Exception as exc:
                detail = " ".join(str(exc).split())
                if "base64" in detail.lower() or len(detail) > 240:
                    detail = detail[:240]
                summaries.append(
                    {
                        "model": review_model,
                        "phase": phase,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_detail": detail,
                    }
                )
                continue
            summaries.append(
                {
                    "model": review_model,
                    "phase": phase,
                    "status": "parsed",
                    "keys": sorted(str(key) for key in payload.keys()),
                    "confidence": payload.get("confidence"),
                    "reason_codes": [str(code) for code in (payload.get("reason_codes") or [])],
                }
            )
        return summaries

    model = "krea-2-turbo"
    client = VeniceImageClient()
''',
)

replace_once(
    "conftest.py",
    '''        correction_codes = list(dict.fromkeys(anatomy.reason_codes or ["anatomy_qa_disagreement"]))
        attempt_summaries.append(
            {
                "attempt": attempt_index + 1,
                "provider": "image",
                "qa": "passed",
                "anatomy": "failed",
                "reason_codes": correction_codes,
                "checksum": checksum_prefix,
                "bytes": len(result.image_bytes),
            }
        )
''',
    '''        correction_codes = list(dict.fromkeys(anatomy.reason_codes or ["anatomy_qa_disagreement"]))
        anatomy_diagnostics = await diagnose_anatomy_reviewers(
            result.image_bytes,
            requirements.get("anatomical_profile"),
        )
        attempt_summaries.append(
            {
                "attempt": attempt_index + 1,
                "provider": "image",
                "qa": "passed",
                "anatomy": "failed",
                "reason_codes": correction_codes,
                "anatomy_diagnostics": anatomy_diagnostics,
                "checksum": checksum_prefix,
                "bytes": len(result.image_bytes),
            }
        )
''',
)
