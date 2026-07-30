from pathlib import Path


path = Path("conftest.py")
text = path.read_text(encoding="utf-8")
text = text.replace("import json\n", "", 1)
text = text.replace("    from app.llm.vision_client import analyze_image_bytes_with_venice\n", "", 1)
text = text.replace(
    '''        ADULT_ANATOMY_PROFILE_QA_PROMPT,\n        ADULT_ANATOMY_QA_SCHEMA,\n        ADULT_ANATOMY_STRUCTURE_QA_PROMPT,\n        corrective_prompt_for_reasons,\n''',
    '''        _configured_vision_reviewer_models,\n        corrective_prompt_for_reasons,\n''',
    1,
)
old_helper = '''    async def diagnose_anatomy_reviewers(image_bytes: bytes, anatomical_profile: str) -> list[dict]:\n        fallback = settings.vision_fallback_model or settings.vision_model\n        review_plan = [\n            (settings.vision_model, ADULT_ANATOMY_PROFILE_QA_PROMPT, "profile"),\n            (fallback, ADULT_ANATOMY_STRUCTURE_QA_PROMPT, "structure"),\n        ]\n        summaries: list[dict] = []\n        for review_model, review_prompt, phase in review_plan:\n            prompt = (\n                review_prompt\n                + "\\nSchema: "\n                + ADULT_ANATOMY_QA_SCHEMA\n                + "\\nRequirements: "\n                + json.dumps({"anatomical_profile": anatomical_profile}, sort_keys=True)\n            )\n            try:\n                payload = await analyze_image_bytes_with_venice(\n                    image_bytes,\n                    prompt=prompt,\n                    model=review_model,\n                )\n            except Exception as exc:\n                detail = " ".join(str(exc).split())\n                if "base64" in detail.lower() or len(detail) > 240:\n                    detail = detail[:240]\n                summaries.append(\n                    {\n                        "model": review_model,\n                        "phase": phase,\n                        "status": "error",\n                        "error_type": type(exc).__name__,\n                        "error_detail": detail,\n                    }\n                )\n                continue\n            summaries.append(\n                {\n                    "model": review_model,\n                    "phase": phase,\n                    "status": "parsed",\n                    "keys": sorted(str(key) for key in payload.keys()),\n                    "confidence": payload.get("confidence"),\n                    "reason_codes": [str(code) for code in (payload.get("reason_codes") or [])],\n                }\n            )\n        return summaries\n\n'''
if old_helper not in text:
    raise SystemExit("old manual anatomy diagnostic helper not found")
text = text.replace(old_helper, "", 1)
old_call = '''        anatomy_diagnostics = await diagnose_anatomy_reviewers(\n            result.image_bytes,\n            requirements.get("anatomical_profile"),\n        )\n'''
new_call = '''        try:\n            reviewer_limit = int(\n                getattr(settings, "image_generation_anatomy_max_reviewer_models", 3) or 3\n            )\n        except (TypeError, ValueError):\n            reviewer_limit = 3\n        anatomy_diagnostics = {\n            "resolved_reviewer_models": _configured_vision_reviewer_models(\n                settings,\n                max_models=reviewer_limit,\n            ),\n            "consensus_model": anatomy.model,\n            "qa_passes": list(getattr(anatomy, "qa_passes", []) or []),\n            "partial_qa_passes": list(\n                getattr(anatomy, "partial_qa_passes", []) or []\n            ),\n            "reviewer_failures": list(\n                getattr(anatomy, "reviewer_failures", []) or []\n            ),\n        }\n'''
if old_call not in text:
    raise SystemExit("old manual diagnostic call not found")
path.write_text(text.replace(old_call, new_call, 1), encoding="utf-8")

Path("tests/test_live_gate_uses_production_reviewer_pool.py").write_text(
    '''from pathlib import Path\n\n\ndef test_live_gate_reports_actual_production_reviewer_pool_metadata():\n    source = Path("conftest.py").read_text(encoding="utf-8")\n\n    assert "diagnose_anatomy_reviewers" not in source\n    assert "analyze_image_bytes_with_venice" not in source\n    assert "_configured_vision_reviewer_models" in source\n    assert '"resolved_reviewer_models"' in source\n    assert '"qa_passes"' in source\n    assert '"partial_qa_passes"' in source\n    assert '"reviewer_failures"' in source\n''',
    encoding="utf-8",
)
