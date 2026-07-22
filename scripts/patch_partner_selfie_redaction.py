from pathlib import Path

path = Path('app/services/semantic_image_intent_router.py')
text = path.read_text(encoding='utf-8')
old = "    extracted = sorted(k for k, v in vi.items() if k != 'confidence' and v not in (None, False, \"\", [], {}) and not (k == 'natural_capture_required' and v is True))\n"
new = "    redacted_field_names={'identity_continuity_required','scene_context_summary'}\n    extracted = sorted(k for k, v in vi.items() if k not in redacted_field_names and k != 'confidence' and v not in (None, False, \"\", [], {}) and not (k == 'natural_capture_required' and v is True))\n"
if new not in text:
    if old not in text:
        raise RuntimeError('shadow redaction target not found')
    text = text.replace(old, new, 1)
path.write_text(text, encoding='utf-8')
