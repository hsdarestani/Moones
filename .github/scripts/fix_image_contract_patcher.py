from pathlib import Path

path = Path('.github/scripts/patch_image_contract_regressions.py')
text = path.read_text()
old = '''qa = replace_once(
    qa,
    '"requested_scene_visible":true,',
    '"requested_nudity_visible":true,"requested_scene_visible":true,',
    "QA schema nudity field",
)
'''
new = '''qa = qa.replace(
    '"requested_scene_visible":true,',
    '"requested_nudity_visible":true,"requested_scene_visible":true,',
    1,
)
'''
if old not in text:
    raise RuntimeError('target patcher block not found')
path.write_text(text.replace(old, new, 1))
