from app.services.generated_media_archive_service import GeneratedMediaArchiveService


def test_archive_service_has_retry_without_provider_hook():
    assert hasattr(GeneratedMediaArchiveService, 'retry_archive')
