import asyncio
from types import SimpleNamespace

from app.llm import image_client as image_client_module
from app.llm.image_client import (
    VENICE_SEED_MIN,
    VeniceImageClient,
    adapt_provider_prompts,
)
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
        base_seed=VENICE_SEED_MIN,
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
        message_id=101,
        user_request=request,
    )
    return compile_image_prompt(plan)


def test_lustify_prompt_is_compacted_without_losing_hard_requirements():
    compiled = _compiled_exact_request()
    assert len(compiled.positive_prompt) > 1200

    prompt, negative, diagnostics = adapt_provider_prompts(
        "lustify-sdxl", compiled.positive_prompt, compiled.negative_prompt
    )

    assert len(prompt) <= 1200
    assert len(negative) <= 1200
    assert diagnostics["provider_prompt_compacted"] is True
    assert diagnostics["original_prompt_chars"] > diagnostics["provider_prompt_chars"]
    lower = prompt.lower()
    assert "exactly one fictional adult" in lower
    assert "visibly fully nude" in lower
    assert "head to feet" in lower
    assert "mirror selfie" in lower
    assert "same stored fictional adult identity" in lower
    assert "female adult anatomy" in lower


def test_modern_image_model_prompt_is_unchanged():
    prompt = "short prompt"
    negative = "watermark"
    adapted_prompt, adapted_negative, diagnostics = adapt_provider_prompts(
        "seedream-v5-lite", prompt, negative
    )
    assert adapted_prompt == prompt
    assert adapted_negative == negative
    assert diagnostics["provider_prompt_compacted"] is False
    assert diagnostics["provider_prompt_limit"] is None


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
        return FakeResponse(data={"data": [{"id": "lustify-sdxl"}]})

    async def post(self, *args, **kwargs):
        self.payload = kwargs["json"]
        return FakeResponse(
            headers={"content-type": "image/png", "x-request-id": "req-1"},
            content=b"\x89PNG\r\n\x1a\nprovider-test",
        )


def test_venice_client_sends_compacted_lustify_payload():
    async def run():
        image_client_module._MODEL_CACHE_IDS = None
        image_client_module._MODEL_CACHE_EXPIRES_AT = 0.0
        fake = FakeHTTPClient()
        client = VeniceImageClient(api_key="test", client=fake, max_attempts=1)
        compiled = _compiled_exact_request()
        response = await client.generate(
            compiled.positive_prompt,
            compiled.negative_prompt,
            width=1024,
            height=1280,
            seed=123,
            model="lustify-sdxl",
        )
        assert len(fake.payload["prompt"]) <= 1200
        assert fake.payload["safe_mode"] is False
        assert response.metadata["provider_prompt_compacted"] is True
        assert response.metadata["provider_prompt_chars"] == len(fake.payload["prompt"])

    asyncio.run(run())
