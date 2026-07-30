from __future__ import annotations

import asyncio


def pytest_sessionstart(session) -> None:
    """One-off production diagnostic; removed immediately after the protected probe."""
    from app.core.config import get_settings
    from app.llm.image_client import VeniceImageClient

    settings = get_settings()
    if not settings.venice_api_key:
        print("OPS_PREFLIGHT_VENICE_START api_key_present=false", flush=True)
        return

    async def run_probe() -> None:
        client = VeniceImageClient(max_attempts=1)
        configured: list[str] = []
        for value in (
            getattr(settings, "image_generation_model", None),
            getattr(settings, "image_generation_fallback_model", None),
            *((getattr(settings, "image_generation_emergency_models", "") or "").split(",")),
        ):
            model = str(value or "").strip()
            if model and model not in configured:
                configured.append(model)

        print(
            f"OPS_PREFLIGHT_VENICE_START api_key_present=true base_url={settings.venice_api_base_url} configured={configured}",
            flush=True,
        )
        try:
            models = await client.available_image_models(ttl_seconds=1)
            available = sorted(models or [])
            print(
                "OPS_PREFLIGHT_VENICE_DISCOVERY "
                f"ok=true count={len(available)} configured_available={[m for m in configured if m in set(available)]} "
                f"sample={available[:20]}",
                flush=True,
            )
        except Exception as exc:
            print(
                "OPS_PREFLIGHT_VENICE_DISCOVERY "
                f"ok=false error_type={type(exc).__name__} error={str(exc)[:1000]!r}",
                flush=True,
            )

        success = False
        for model in configured:
            try:
                result = await client.generate(
                    "A realistic casual smartphone portrait of one fictional adult woman indoors, fully clothed, natural daylight, no text, no watermark",
                    "text, watermark, extra people, duplicated person, distorted anatomy",
                    width=1024,
                    height=1280,
                    seed=24681357,
                    model=model,
                )
                print(
                    "OPS_PREFLIGHT_VENICE_RESULT "
                    f"model={model} ok=true mime={result.mime_type} bytes={len(result.image_bytes)} "
                    f"response_type={result.response_type} request_id={result.request_id} metadata={result.metadata}",
                    flush=True,
                )
                success = True
                break
            except Exception as exc:
                print(
                    "OPS_PREFLIGHT_VENICE_RESULT "
                    f"model={model} ok=false error_type={type(exc).__name__} "
                    f"error_code={getattr(exc, 'code', None)} retryable={getattr(exc, 'retryable', None)} "
                    f"error={str(exc)[:1500]!r}",
                    flush=True,
                )
        print(f"OPS_PREFLIGHT_VENICE_COMPLETE success={success}", flush=True)

    asyncio.run(run_probe())
