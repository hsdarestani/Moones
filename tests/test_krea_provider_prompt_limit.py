import asyncio
from types import SimpleNamespace

from app.llm import image_client as image_client_module
from app.llm.image_client import VeniceImageClient, adapt_provider_prompts
from app.services.generated_image_qa_service import corrective_prompt_for_reasons
from app.services.image_pipeline_v2 import (
    PolicyDecision,
    SafetyDecision,
    compile_image_prompt,
    construct_resolved_plan,
    ensure_visual_profile_v2,
    merge_image_intent,
    normalize_request_v2,
    parse_image_intent,
)


class DummyDB:
    def flush(self):
        pass


def _compiled_exact_request():
    request = "یه عکس تمام‌قد کاملاً لخت جلوی آینه توی اتاقت بده"
    user = SimpleNamespace(partner_gender="دختر")
    profile = SimpleNamespace(
        profile_json={},
        anatomical_profile=None,
        gender_presentation="feminine",
        base_seed=1,
        user_id=1,
        version=3,
        partner_name="مونس",
        fictional_age=25,
        face_description="oval face, softly defined jawline, natural facial proportions",
        hair_description="dark shoulder-length hair with a natural hairline",
        eye_description="dark almond-shaped eyes and natural eyebrows",
        skin_description="olive skin with natural texture",
        body_description="average adult feminine build with natural proportions",
        height_impression="average height",
        distinguishing_details="natural eyebrows",
        updated_at=None,
    )
    profile = ensure_visual_profile_v2(DummyDB(), user, profile)
    intent = parse_image_intent(normalize_request_v2(request))
    merged = merge_image_intent(intent, recent_context=[], memory_context=[], routine_context={})
    plan = construct_resolved_plan(
        intent,
        merged,
        SafetyDecision(PolicyDecision.ALLOW),
        profile,
        message_id=191,
        user_request=request,
    )
    return plan, compile_image_prompt(plan)


def test_exact_minimal_krea_request_is_unchanged_when_already_below_limit():
    _, compiled = _compiled_exact_request()
    assert len(compiled.positive_prompt) < 4800

    prompt, negative, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", compiled.positive_prompt, compiled.negative_prompt
    )

    assert prompt == compiled.positive_prompt
    assert negative == compiled.negative_prompt
    assert diagnostics["provider_prompt_compacted"] is False
    assert diagnostics["provider_prompt_limit"] == 4800


def test_context_heavy_krea_request_is_bounded_below_live_5000_character_limit():
    _, compiled = _compiled_exact_request()
    overflow_context = (
        " Additional low-priority world-memory detail about ordinary room atmosphere, "
        "decor, routine continuity, and noncritical background texture."
    ) * 24
    long_prompt = compiled.positive_prompt + overflow_context
    assert len(long_prompt) > 5000

    prompt, negative, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", long_prompt, compiled.negative_prompt
    )

    assert len(prompt) <= 4800
    assert len(prompt) < 5000
    assert diagnostics["provider_prompt_compacted"] is True
    assert diagnostics["provider_prompt_limit"] == 4800
    assert diagnostics["original_prompt_chars"] > diagnostics["provider_prompt_chars"]
    lower = prompt.lower()
    assert "exactly one fictional adult" in lower
    assert "visibly fully nude" in lower
    assert "head to feet" in lower or "head-to-feet" in lower
    assert "mirror selfie" in lower
    assert "same stored fictional adult identity" in lower
    assert "female adult anatomy" in lower
    assert negative == compiled.negative_prompt


def test_krea_corrective_retry_keeps_full_body_composition_contract():
    plan, compiled = _compiled_exact_request()
    correction = corrective_prompt_for_reasons(
        ["framing_mismatch", "missing_feet", "cropped_body"],
        expected_subject_count=1,
        identity_requirements=plan.identity.get("descriptor"),
        photo_contract={"camera_mode": "mirror_selfie"},
    )
    overflow_context = (
        " Additional low-priority conversational memory and ordinary background detail."
    ) * 30
    combined = compiled.positive_prompt + overflow_context + correction
    assert len(combined) > 5000
    prompt, _, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", combined, compiled.negative_prompt
    )

    assert len(prompt) <= 4800
    assert diagnostics["provider_prompt_compacted"] is True
    lower = prompt.lower()
    assert "corrective full-body composition" in lower
    assert "full figure head-to-feet" in lower
    assert "headroom above the hair" in lower
    assert "visible floor below both feet" in lower
    assert "both feet fully visible" in lower
    assert "70 percent" in lower
    assert "no close-up and no crop" in lower


def test_short_krea_and_nonlimited_model_prompts_are_unchanged():
    for model in ("krea-2-turbo", "seedream-v5-lite"):
        prompt, negative, diagnostics = adapt_provider_prompts(
            model, "short personal photo prompt", "watermark, text"
        )
        assert prompt == "short personal photo prompt"
        assert negative == "watermark, text"
        assert diagnostics["provider_prompt_compacted"] is False


class FakeResponse:
    def __init__(self, *, status_code=200, headers=None, content=b"", data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self._data = data or {}
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._data


class FakeHTTPClient:
    def __init__(self):
        self.payload = None

    async def get(self, *args, **kwargs):
        return FakeResponse(data={"data": [{"id": "krea-2-turbo"}]})

    async def post(self, *args, **kwargs):
        self.payload = kwargs["json"]
        return FakeResponse(
            headers={"content-type": "image/png", "x-request-id": "krea-test"},
            content=b"\x89PNG\r\n\x1a\nprovider-test",
        )


def test_venice_client_sends_krea_payload_below_real_provider_limit():
    async def run():
        image_client_module._MODEL_CACHE_IDS = None
        image_client_module._MODEL_CACHE_EXPIRES_AT = 0.0
        fake = FakeHTTPClient()
        client = VeniceImageClient(api_key="test", client=fake, max_attempts=1)
        _, compiled = _compiled_exact_request()
        long_prompt = compiled.positive_prompt + (
            " Additional low-priority world-memory context and ordinary background detail."
            * 30
        )
        assert len(long_prompt) > 5000
        response = await client.generate(
            long_prompt,
            compiled.negative_prompt,
            width=1024,
            height=1280,
            seed=123,
            model="krea-2-turbo",
        )
        assert len(fake.payload["prompt"]) <= 4800
        assert len(fake.payload["prompt"]) < 5000
        assert fake.payload["safe_mode"] is False
        assert response.metadata["provider_prompt_compacted"] is True
        assert response.metadata["provider_prompt_limit"] == 4800
        assert response.metadata["provider_prompt_chars"] == len(fake.payload["prompt"])

    asyncio.run(run())
