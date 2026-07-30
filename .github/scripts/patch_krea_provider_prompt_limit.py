from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:240]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/llm/image_client.py",
    '''# Legacy SDXL-family image models have much smaller practical prompt windows than
# newer image models. Keep the provider payload compact without weakening the
# authoritative internal plan or its validation.
LEGACY_DIFFUSION_PROMPT_LIMITS = {
    "lustify-sdxl": 1200,
    "lustify-v7": 1200,
    "lustify-v8": 1200,
}
''',
    '''# Venice enforces model-specific prompt limits. Keep the authoritative internal
# plan intact for validation/QA while adapting only the provider-bound strings.
# Krea's documented live limit is 5,000 characters; use headroom for any provider
# normalization. Legacy Lustify models need much tighter practical prompts.
PROVIDER_POSITIVE_PROMPT_LIMITS = {
    "krea-2-turbo": 4800,
    "lustify-sdxl": 1200,
    "lustify-v7": 1200,
    "lustify-v8": 1200,
}
PROVIDER_NEGATIVE_PROMPT_LIMITS = {
    "krea-2-turbo": 4800,
    "lustify-sdxl": 1200,
    "lustify-v7": 1200,
    "lustify-v8": 1200,
}
''',
)

replace_once(
    "app/llm/image_client.py",
    '''    if "photorealistic" in lower or "natural skin texture" in lower or "believable personal-photo" in lower:
        essentials.append("Photorealistic believable personal photo with natural skin texture, natural lighting, realistic posture, and no artificial catalogue look.")

    segments = [segment.strip() for segment in re.split(r"(?<=[.!?])\\s+", prompt) if segment.strip()]
''',
    '''    if "photorealistic" in lower or "natural skin texture" in lower or "believable personal-photo" in lower:
        essentials.append("Photorealistic believable personal photo with natural skin texture, natural lighting, realistic posture, and no artificial catalogue look.")
    if "visible floor below both feet" in lower or "subject no more than about 70 percent" in lower:
        essentials.append("Corrective full-body composition: full body visible and full figure head-to-feet inside a portrait 4:5 mirror frame, with headroom above the hair, visible floor below both feet, both feet fully visible, subject at most about 70 percent of frame height, camera farther away, no close-up and no crop.")

    segments = [segment.strip() for segment in re.split(r"(?<=[.!?])\\s+", prompt) if segment.strip()]
''',
)

replace_once(
    "app/llm/image_client.py",
    '''        "user-requested visual details:",
    )
''',
    '''        "user-requested visual details:",
        "strict partner-photo correction:",
        "correct the framing exactly:",
        "do not return a clothed",
        "preserve the exact stored face family",
        "preserve the stored adult identity and anatomical profile",
    )
''',
)

replace_once(
    "app/llm/image_client.py",
    '''def adapt_provider_prompts(model: str, prompt: str, negative_prompt: str) -> tuple[str, str, dict]:
    limit = LEGACY_DIFFUSION_PROMPT_LIMITS.get(str(model or "").strip())
    if not limit:
        return prompt, negative_prompt, {
            "provider_prompt_compacted": False,
            "provider_prompt_limit": None,
            "original_prompt_chars": len(prompt or ""),
            "provider_prompt_chars": len(prompt or ""),
            "original_negative_prompt_chars": len(negative_prompt or ""),
            "provider_negative_prompt_chars": len(negative_prompt or ""),
        }
    compact_prompt = _compact_positive_prompt(prompt, limit)
    compact_negative = _compact_negative_prompt(negative_prompt, limit)
    return compact_prompt, compact_negative, {
        "provider_prompt_compacted": compact_prompt != prompt or compact_negative != negative_prompt,
        "provider_prompt_limit": limit,
        "original_prompt_chars": len(prompt or ""),
        "provider_prompt_chars": len(compact_prompt),
        "original_negative_prompt_chars": len(negative_prompt or ""),
        "provider_negative_prompt_chars": len(compact_negative),
    }
''',
    '''def adapt_provider_prompts(model: str, prompt: str, negative_prompt: str) -> tuple[str, str, dict]:
    normalized_model = str(model or "").strip()
    positive_limit = PROVIDER_POSITIVE_PROMPT_LIMITS.get(normalized_model)
    negative_limit = PROVIDER_NEGATIVE_PROMPT_LIMITS.get(normalized_model)
    compact_prompt = (
        _compact_positive_prompt(prompt, positive_limit)
        if positive_limit
        else prompt
    )
    compact_negative = (
        _compact_negative_prompt(negative_prompt, negative_limit)
        if negative_limit
        else negative_prompt
    )
    return compact_prompt, compact_negative, {
        "provider_prompt_compacted": compact_prompt != prompt or compact_negative != negative_prompt,
        "provider_prompt_limit": positive_limit,
        "provider_negative_prompt_limit": negative_limit,
        "original_prompt_chars": len(prompt or ""),
        "provider_prompt_chars": len(compact_prompt or ""),
        "original_negative_prompt_chars": len(negative_prompt or ""),
        "provider_negative_prompt_chars": len(compact_negative or ""),
    }
''',
)

Path("tests/test_krea_provider_prompt_limit.py").write_text(
    '''import asyncio
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


def test_exact_krea_request_is_bounded_below_live_5000_character_limit():
    plan, compiled = _compiled_exact_request()
    assert len(compiled.positive_prompt) > 5000

    prompt, negative, diagnostics = adapt_provider_prompts(
        "krea-2-turbo", compiled.positive_prompt, compiled.negative_prompt
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
    combined = compiled.positive_prompt + correction
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
            content=b"\\x89PNG\\r\\n\\x1a\\nprovider-test",
        )


def test_venice_client_sends_krea_payload_below_real_provider_limit():
    async def run():
        image_client_module._MODEL_CACHE_IDS = None
        image_client_module._MODEL_CACHE_EXPIRES_AT = 0.0
        fake = FakeHTTPClient()
        client = VeniceImageClient(api_key="test", client=fake, max_attempts=1)
        _, compiled = _compiled_exact_request()
        response = await client.generate(
            compiled.positive_prompt,
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
''',
    encoding="utf-8",
)
