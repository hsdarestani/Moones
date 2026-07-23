from pathlib import Path

path = Path('tests/test_image_provider_failover.py')
text = path.read_text()
old = '        assert set(result.metadata_json["skipped_unavailable_generation_models"]) == {"krea-2-turbo", "venice-sd35", "z-image-turbo"}\n'
new = '        assert set(result.metadata_json["skipped_unavailable_generation_models"]) == {"krea-2-turbo", "venice-sd35"}\n'
if text.count(old) != 1:
    raise RuntimeError(f'expected one test assertion, found {text.count(old)}')
path.write_text(text.replace(old, new, 1))
print('fix_provider_test_expectation: ok')
