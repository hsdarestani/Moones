import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.llm.image_client import (
    DEFAULT_IMAGE_MODEL,
    FALLBACK_IMAGE_MODEL,
)
from app.models.user import User
from app.models.image_generation import (
    ImageGenerationJob,
    ImageGenerationArtifact,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:"
    )

    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            ImageGenerationJob.__table__,
            ImageGenerationArtifact.__table__,
        ],
    )

    return sessionmaker(
        bind=engine
    )()


def _valid_png() -> bytes:
    output = BytesIO()

    Image.new(
        "RGB",
        (32, 32),
    ).save(
        output,
        format="PNG",
    )

    return output.getvalue()


class FakeImageClient:
    def __init__(self):
        self.calls = []

    async def generate(
        self,
        prompt,
        negative_prompt,
        *,
        width,
        height,
        seed,
        model=None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "negative_prompt": (
                    negative_prompt
                ),
                "seed": seed,
                "model": model,
            }
        )

        return SimpleNamespace(
            image_bytes=_valid_png(),
            mime_type="image/png",
            request_id=(
                f"request-{len(self.calls)}"
            ),
            width=width,
            height=height,
            latency_seconds=0.01,
            response_type="binary",
            metadata={
                "seed_used": seed,
            },
        )


class FakeTelegram:
    def __init__(self):
        self.calls = 0

    async def send_photo_bytes(
        self,
        *args,
        **kwargs,
    ):
        self.calls += 1
        return 123


def _job(session):
    user = User(
        telegram_id=99123
    )

    session.add(user)
    session.flush()

    job = ImageGenerationJob(
        idempotency_key="qa-job",
        correlation_id="qa-job",
        user_id=user.id,
        chat_id=123,
        status="processing",
        prompt=(
            "a solitary fictional adult "
            "woman photographed alone"
        ),
        negative_prompt=(
            "multiple people, duplicate subject"
        ),
        seed=12345,
        width=1024,
        height=1280,
    )

    session.add(job)
    session.commit()

    return job


def test_visual_qa_retries_then_sends(
    monkeypatch,
):
    async def run():
        import app.services.image_generation_service as svc

        results = [
            {
                "person_count": 3,
                "single_continuous_frame": True,
                "has_panel_layout": False,
                "has_duplicate_or_reflection": False,
                "passed": False,
            },
            {
                "person_count": 1,
                "single_continuous_frame": True,
                "has_panel_layout": False,
                "has_duplicate_or_reflection": False,
                "passed": True,
            },
        ]

        async def fake_qa(*args, **kwargs):
            return results.pop(0)

        async def fake_archive(
            self,
            db,
            job,
        ):
            job.archive_status = "disabled"
            return False

        monkeypatch.setattr(
            svc,
            "assess_generated_image_conformance",
            fake_qa,
        )

        monkeypatch.setattr(
            svc.GeneratedMediaArchiveService,
            "archive_image",
            fake_archive,
        )

        monkeypatch.setattr(
            svc,
            "record_media_delivery",
            lambda *args, **kwargs: None,
        )

        session = _session()
        job = _job(session)
        client = FakeImageClient()
        telegram = FakeTelegram()

        await svc.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
        )

        assert job.status == "sent"
        assert telegram.calls == 1
        assert len(client.calls) == 2

        assert [
            call["model"]
            for call in client.calls
        ] == [
            DEFAULT_IMAGE_MODEL,
            FALLBACK_IMAGE_MODEL,
        ]

        assert (
            client.calls[0]["seed"]
            != client.calls[1]["seed"]
        )

        assert (
            job.metadata_json[
                "visual_qa_final_result"
            ]
            == "passed"
        )

        assert (
            job.metadata_json[
                "visual_qa_retry_count"
            ]
            == 1
        )

    asyncio.run(run())


def test_visual_qa_rejects_after_three_attempts(
    monkeypatch,
):
    async def run():
        import app.services.image_generation_service as svc

        async def fake_qa(*args, **kwargs):
            return {
                "person_count": 3,
                "single_continuous_frame": True,
                "has_panel_layout": False,
                "has_duplicate_or_reflection": False,
                "passed": False,
            }

        monkeypatch.setattr(
            svc,
            "assess_generated_image_conformance",
            fake_qa,
        )

        session = _session()
        job = _job(session)
        client = FakeImageClient()
        telegram = FakeTelegram()

        await svc.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
        )

        assert job.status == "failed"
        assert telegram.calls == 0
        assert len(client.calls) == 3

        assert [
            call["model"]
            for call in client.calls
        ] == [
            DEFAULT_IMAGE_MODEL,
            FALLBACK_IMAGE_MODEL,
            FALLBACK_IMAGE_MODEL,
        ]

        assert job.error_code == (
            "provider_failure"
        )

        assert "visual_qa_failed" in (
            job.error_message or ""
        )

        assert (
            job.metadata_json[
                "visual_qa_final_result"
            ]
            == "rejected"
        )

    asyncio.run(run())


def test_visual_qa_unavailable_does_not_send(
    monkeypatch,
):
    async def run():
        import app.services.image_generation_service as svc

        async def unavailable_qa(
            *args,
            **kwargs,
        ):
            raise RuntimeError(
                "vision temporarily unavailable"
            )

        monkeypatch.setattr(
            svc,
            "assess_generated_image_conformance",
            unavailable_qa,
        )

        session = _session()
        job = _job(session)
        client = FakeImageClient()
        telegram = FakeTelegram()

        await svc.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
        )

        assert job.status == "failed"
        assert telegram.calls == 0
        assert len(client.calls) == 1

        assert (
            job.metadata_json[
                "visual_qa_final_result"
            ]
            == "unavailable"
        )

        assert (
            "visual_qa_unavailable"
            in (job.error_message or "")
        )

    asyncio.run(run())
