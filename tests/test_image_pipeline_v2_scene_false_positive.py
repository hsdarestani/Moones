from app.services import image_pipeline_v2 as v2


def test_khodet_does_not_match_car_scene():
    intent = v2.parse_image_intent(
        v2.normalize_request_v2(
            "فقط خودت ایستاده و تمام قد عکس بده"
        )
    )

    assert intent.scene.scene_key != "car"
    assert not any(
        match.category == "scene"
        and match.canonical == "car"
        for match in intent.parse_coverage.semantic_matches
    )


def test_khodro_still_matches_car_scene():
    intent = v2.parse_image_intent(
        v2.normalize_request_v2(
            "داخل خودرو عکس بده"
        )
    )

    assert intent.scene.scene_key == "car"
