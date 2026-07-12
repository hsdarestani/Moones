from pathlib import Path


def test_generated_media_dashboard_template_contains_required_tabs():
    text = Path('app/templates/admin/generated_media.html').read_text()
    assert 'Images' in text and 'Voices' in text and 'Safety and archive settings' in text
