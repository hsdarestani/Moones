from pathlib import Path

path=Path('app/services/image_generation_service.py')
text=path.read_text()
old='''        prior=v2.parse_image_intent(v2.normalize_request_v2(text))
        if not prior.is_image_request or not (prior.scene.scene_key or prior.scene.location):
            continue
        intent.scene.scene_key=prior.scene.scene_key
        intent.scene.location=prior.scene.location or prior.scene.scene_key
        intent.scene.environment_type=prior.scene.environment_type
        intent.scene.privacy=prior.scene.privacy
'''
new='''        prior=v2.parse_image_intent(v2.normalize_request_v2(text))
        scene_key=prior.scene.scene_key
        location=prior.scene.location
        environment_type=prior.scene.environment_type
        privacy=prior.scene.privacy
        if not (scene_key or location):
            normalized=" ".join(text.replace("‌", " ").replace("ي", "ی").replace("ك", "ک").lower().split())
            aliases=(("کافه", "cafe", "cafe", "public_indoor", "public"), ("خونه", "home", "home", "private_indoor", "private"), ("خانه", "home", "home", "private_indoor", "private"), ("خیابون", "street", "street", "public_outdoor", "public"), ("خیابان", "street", "street", "public_outdoor", "public"), ("پارک", "park", "park", "public_outdoor", "public"), ("ماشین", "car", "car", "vehicle", "private"))
            match=next((row for row in aliases if row[0] in normalized), None)
            if match:
                _, scene_key, location, environment_type, privacy=match
        if not prior.is_image_request or not (scene_key or location):
            continue
        intent.scene.scene_key=scene_key
        intent.scene.location=location or scene_key
        intent.scene.environment_type=environment_type
        intent.scene.privacy=privacy
'''
if text.count(old) != 1:
    raise RuntimeError(f'scene fallback anchor count={text.count(old)}')
path.write_text(text.replace(old,new,1))
