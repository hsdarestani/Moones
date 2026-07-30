from pathlib import Path

path = Path("app/services/generated_image_qa_service.py")
text = path.read_text(encoding="utf-8")
old = '''prompt=review_prompt + "
Schema: " + ADULT_ANATOMY_QA_SCHEMA + "
Requirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)'''
new = '''prompt=review_prompt + "\\nSchema: " + ADULT_ANATOMY_QA_SCHEMA + "\\nRequirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)'''
if old not in text:
    raise SystemExit("generated broken anatomy prompt string not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
