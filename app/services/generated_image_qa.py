from __future__ import annotations

import base64
import json
import re
from io import BytesIO

import httpx
from PIL import Image

from app.core.config import get_settings
from app.llm.client import extract_text_from_venice_response


GENERATED_IMAGE_QA_VERSION = "generated-image-qa-v1"

GENERATED_IMAGE_QA_PROMPT = """
You are a strict machine-vision quality-control module.

Inspect the generated image only for layout and the number of visible
human figures. Do not describe nudity, anatomy, attractiveness,
identity, ethnicity, health, or any other personal attribute.

Count every visible human figure, including:
- foreground people
- background people
- duplicated bodies
- cloned faces attached to separate bodies
- human reflections in mirrors
- repeated versions of the same person

A partial reflection or clearly separate duplicated human figure must
be treated as an additional visible person.

Return JSON only in exactly this structure:

{
  "person_count": 1,
  "single_continuous_frame": true,
  "has_panel_layout": false,
  "has_duplicate_or_reflection": false,
  "confidence": "high"
}
""".strip()


def _extract_json(text: str) -> dict:
    match = re.search(
        r"\{.*\}",
        text or "",
        re.S,
    )

    payload = (
        match.group(0)
        if match
        else text
    )

    return json.loads(payload)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value

    return str(value).strip().casefold() in {
        "true",
        "1",
        "yes",
    }


def _prepare_preview(
    image_bytes: bytes,
) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail(
            (768, 768)
        )

        output = BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=82,
            optimize=True,
        )

        return output.getvalue()


async def assess_generated_image_conformance(
    image_bytes: bytes,
    mime_type: str | None = None,
    *,
    model: str | None = None,
) -> dict:
    settings = get_settings()

    if not settings.venice_api_key:
        raise RuntimeError(
            "visual_qa_missing_api_key"
        )

    preview = _prepare_preview(
        image_bytes
    )

    encoded = base64.b64encode(
        preview
    ).decode("ascii")

    selected_model = (
        model
        or settings.vision_model
    )

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            GENERATED_IMAGE_QA_PROMPT
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                "data:image/jpeg;base64,"
                                + encoded
                            )
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 300,
    }

    timeout = httpx.Timeout(
        connect=10,
        read=45,
        write=30,
        pool=10,
    )

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:
        response = await client.post(
            (
                settings
                .venice_api_base_url
                .rstrip("/")
                + "/chat/completions"
            ),
            headers={
                "Authorization": (
                    "Bearer "
                    + settings.venice_api_key
                ),
                "Content-Type": (
                    "application/json"
                ),
            },
            json=payload,
        )

    if response.status_code >= 400:
        raise RuntimeError(
            "visual_qa_provider_error:"
            + response.text[:300]
        )

    response_text, _ = (
        extract_text_from_venice_response(
            response.json()
        )
    )

    result = _extract_json(
        response_text
    )

    try:
        person_count = int(
            result.get(
                "person_count",
                0,
            )
        )
    except (TypeError, ValueError):
        person_count = 0

    single_continuous_frame = _as_bool(
        result.get(
            "single_continuous_frame"
        )
    )

    has_panel_layout = _as_bool(
        result.get(
            "has_panel_layout"
        )
    )

    has_duplicate_or_reflection = _as_bool(
        result.get(
            "has_duplicate_or_reflection"
        )
    )

    passed = (
        person_count == 1
        and single_continuous_frame
        and not has_panel_layout
        and not has_duplicate_or_reflection
    )

    return {
        "qa_version": (
            GENERATED_IMAGE_QA_VERSION
        ),
        "model": selected_model,
        "person_count": person_count,
        "single_continuous_frame": (
            single_continuous_frame
        ),
        "has_panel_layout": (
            has_panel_layout
        ),
        "has_duplicate_or_reflection": (
            has_duplicate_or_reflection
        ),
        "confidence": str(
            result.get(
                "confidence",
                "unknown",
            )
        ),
        "passed": passed,
    }
