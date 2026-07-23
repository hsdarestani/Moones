from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


client_path = Path('app/llm/image_client.py')
client = client_path.read_text()
client = replace_once(
    client,
    '''        "return_binary": True,
        "format": "webp",
    }
    if model in _RESOLUTION_TIER_MODELS:
''',
    '''        "return_binary": True,
    }
    if model == "krea-2-turbo":
        width, height = validate_image_dimensions(width, height, model=model)
        return {
            **base,
            "width": width,
            "height": height,
            "steps": DEFAULT_STEPS,
            "cfg_scale": DEFAULT_CFG_SCALE,
        }
    if model in _RESOLUTION_TIER_MODELS:
''',
    'legacy krea and optional format payload',
)
client_path.write_text(client)

state_path = Path('tests/test_image_visual_state.py')
state = state_path.read_text()
state = replace_once(
    state,
    '''def test_provider_payload_uses_selected_dimensions_and_same_tier():
    payload=venice_image_payload('p','n',width=1280,height=1024)
    assert payload['width'] == 1280 and payload['height'] == 1024
    assert image_resolution_tier(1024,1280) == image_resolution_tier(1280,1024) == 'image_1k'
''',
    '''def test_provider_payload_uses_selected_orientation_and_same_tier():
    payload=venice_image_payload('p','n',width=1280,height=1024)
    assert payload['aspect_ratio'] == '5:4' and payload['resolution'] == '1K'
    assert 'width' not in payload and 'height' not in payload
    assert image_resolution_tier(1024,1280) == image_resolution_tier(1280,1024) == 'image_1k'
''',
    'seedream orientation payload test',
)
state_path.write_text(state)
print('patch_provider_payload_compat: ok')
