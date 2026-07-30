from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# Product config: allow one bounded Seedream corrective attempt after QA rejection.
config_path = Path("app/core/config.py")
config = config_path.read_text(encoding="utf-8")
config = replace_once(
    config,
    '    image_generation_adult_max_generation_attempts: int = 3\n',
    '    image_generation_adult_max_generation_attempts: int = 4\n',
    "adult max generation attempts",
)
config_path.write_text(config, encoding="utf-8")


# Runtime generation plan: Krea base/correction, then Seedream base/correction.
service_path = Path("app/services/image_generation_service.py")
service = service_path.read_text(encoding="utf-8")
service = replace_once(
    service,
    """        # Only Krea gets one composition-only retry. Seedream is the final\n        # fallback and is never silently repeated or followed by another model.\n        if adult_generation and model == ADULT_PRIMARY_GENERATION_MODEL:\n            attempts.append((model, 1))\n""",
    """        # Each allowed adult generator gets at most one bounded QA-driven\n        # corrective retry. Krea keeps its identity seed; Seedream receives a new\n        # deterministic seed so an invented prop/collage is not reproduced. No\n        # third model may enter the route.\n        if adult_generation and model in ADULT_ALLOWED_GENERATION_MODELS:\n            attempts.append((model, 1))\n""",
    "attempt plan corrective models",
)
service = replace_once(
    service,
    "max_generation_attempts = int(getattr(settings, 'image_generation_adult_max_generation_attempts', 3) or 3) if adult_generation else len(model_plan)",
    "max_generation_attempts = int(getattr(settings, 'image_generation_adult_max_generation_attempts', 4) or 4) if adult_generation else len(model_plan)",
    "worker default max attempts",
)
service_path.write_text(service, encoding="utf-8")


# Protected live gate must exercise the exact production policy.
gate_path = Path("conftest.py")
gate = gate_path.read_text(encoding="utf-8")
gate = replace_once(
    gate,
    '''    attempt_plan = [\n        ("krea-2-turbo", 0),\n        ("krea-2-turbo", 1),\n        ("seedream-v5-lite", 0),\n    ]\n''',
    '''    attempt_plan = [\n        ("krea-2-turbo", 0),\n        ("krea-2-turbo", 1),\n        ("seedream-v5-lite", 0),\n        ("seedream-v5-lite", 1),\n    ]\n''',
    "live attempt plan",
)
gate_path.write_text(gate, encoding="utf-8")


# Permanent plan tests.
plan_test_path = Path("tests/test_krea_adult_generation_plan.py")
plan_test = plan_test_path.read_text(encoding="utf-8")
plan_test = replace_once(
    plan_test,
    '    assert settings.image_generation_adult_max_generation_attempts == 3\n',
    '    assert settings.image_generation_adult_max_generation_attempts == 4\n',
    "settings max attempts test",
)
plan_test = replace_once(
    plan_test,
    '''def test_adult_attempt_plan_is_krea_same_model_retry_then_seedream():\n    assert build_generation_attempt_plan(\n        ["krea-2-turbo", "seedream-v5-lite"],\n        adult_generation=True,\n        max_attempts=3,\n    ) == [\n        ("krea-2-turbo", 0),\n        ("krea-2-turbo", 1),\n        ("seedream-v5-lite", 0),\n    ]\n''',
    '''def test_adult_attempt_plan_is_krea_retry_then_seedream_retry_only():\n    assert build_generation_attempt_plan(\n        ["krea-2-turbo", "seedream-v5-lite"],\n        adult_generation=True,\n        max_attempts=4,\n    ) == [\n        ("krea-2-turbo", 0),\n        ("krea-2-turbo", 1),\n        ("seedream-v5-lite", 0),\n        ("seedream-v5-lite", 1),\n    ]\n''',
    "attempt plan test",
)
plan_test_path.write_text(plan_test, encoding="utf-8")


contract_path = Path("tests/test_adult_generation_policy_contract.py")
contract = contract_path.read_text(encoding="utf-8")
contract = replace_once(
    contract,
    '    assert \'("seedream-v5-lite", 0)\' in source\n',
    '    assert \'("seedream-v5-lite", 0)\' in source\n    assert \'("seedream-v5-lite", 1)\' in source\n',
    "live gate seedream correction contract",
)
contract_path.write_text(contract, encoding="utf-8")


# Worker-level regression for the exact production failure: Seedream invents a prop,
# then its bounded corrective retry removes it and delivers without refund.
worker_path = Path("tests/test_krea_adult_worker_delivery.py")
worker = worker_path.read_text(encoding="utf-8")
worker = replace_once(
    worker,
    '        image_generation_adult_max_generation_attempts=3,\n',
    '        image_generation_adult_max_generation_attempts=4,\n',
    "worker settings max attempts",
)
worker += r'''


def test_seedream_unrequested_object_gets_corrective_retry_before_refund(monkeypatch):
    import app.services.image_generation_service as service

    async def run():
        session = _session()
        user = User(telegram_id=904)
        session.add(user)
        session.flush()
        job = _job(session, user)
        client = _KreaCropThenPassClient()
        telegram = _Telegram()
        qa_calls = 0

        async def qa(*args, **kwargs):
            nonlocal qa_calls
            qa_calls += 1
            if qa_calls == 1:
                return _qa_result(
                    passed=False,
                    reason_codes=["framing_mismatch", "missing_feet", "cropped_body"],
                )
            if qa_calls == 2:
                return _qa_result(
                    passed=False,
                    reason_codes=["collage_or_split_panel", "multiple_frames", "repeated_subject_panel"],
                )
            if qa_calls == 3:
                return _qa_result(
                    passed=False,
                    reason_codes=["unrequested_foreground_object"],
                )
            return _qa_result(passed=True)

        async def anatomy(*args, **kwargs):
            return _anatomy_pass()

        monkeypatch.setattr(service, "get_settings", _settings)
        monkeypatch.setattr(service, "evaluate_adult_anatomy_image", anatomy)
        monkeypatch.setattr(
            service.GeneratedMediaArchiveService,
            "archive_image",
            lambda *args, **kwargs: asyncio.sleep(0, result=False),
        )

        result = await service.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
            generated_image_qa_evaluator=qa,
        )

        assert result.status == "sent"
        assert [call["model"] for call in client.calls] == [
            "krea-2-turbo",
            "krea-2-turbo",
            "seedream-v5-lite",
            "seedream-v5-lite",
        ]
        assert client.calls[0]["seed"] == client.calls[1]["seed"] == 777777
        assert client.calls[2]["seed"] != client.calls[3]["seed"]
        final_prompt = client.calls[3]["prompt"].lower()
        assert "remove every conspicuous unrequested" in final_prompt
        assert "cup" in final_prompt
        assert result.metadata_json["final_generation_model"] == "seedream-v5-lite"
        assert result.metadata_json["fallback_model_used"] is True
        assert result.metadata_json["effective_generation_attempt_plan"] == [
            {"model": "krea-2-turbo", "correction_round": 0},
            {"model": "krea-2-turbo", "correction_round": 1},
            {"model": "seedream-v5-lite", "correction_round": 0},
            {"model": "seedream-v5-lite", "correction_round": 1},
        ]
        assert result.metadata_json["anatomy_qa_passed"] is True
        assert len(telegram.photos) == 1
        assert not telegram.texts

    asyncio.run(run())
'''
worker_path.write_text(worker, encoding="utf-8")
