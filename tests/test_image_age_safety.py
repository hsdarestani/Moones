from app.services.image_prompt_engine import _parse_minimum_age


def test_conservative_age_range_parsing():
    cases = {'24': (24, True), '24-30': (24, True), '18 تا 25': (18, False), '20': (20, False), 'بالای 21': (22, True), 'زیر 21': (21, False), '': (None, False), 'نامشخص': (None, False)}
    for raw, expected in cases.items():
        assert _parse_minimum_age(raw) == expected
