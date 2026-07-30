from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text(encoding="utf-8")

qa = replace_once(
    qa,
    "import hashlib, json, logging\nfrom dataclasses import dataclass, asdict\n",
    "import hashlib, json, logging, math, statistics\nfrom dataclasses import dataclass, asdict\nfrom io import BytesIO\n\nfrom PIL import Image, ImageOps\n",
    "QA detector imports",
)

qa = replace_once(
    qa,
    "'third_person_viewpoint','visible_phone_in_non_mirror_selfie'\n",
    "'third_person_viewpoint','visible_phone_in_non_mirror_selfie','collage_or_split_panel','repeated_subject_panel','multiple_frames','unrequested_foreground_object'\n",
    "QA reason codes",
)

qa = replace_once(
    qa,
    "    visible_held_phone_detected: bool = False\n\n    def to_metadata",
    "    visible_held_phone_detected: bool = False\n    single_frame_image: bool | None = None\n    collage_or_split_panel_detected: bool = False\n    repeated_subject_panel_detected: bool = False\n    unrequested_foreground_object_visible: bool = False\n    unrequested_foreground_object_labels: list[str] | None = None\n    deterministic_split_panel_diagnostics: dict | None = None\n\n    def to_metadata",
    "QA result fields",
)

qa = replace_once(
    qa,
    "A physically consistent reflection of the same intended partner is not a second person. Schema:",
    "A physically consistent reflection of the same intended partner is not a second person. Every generated result must still be one continuous photographic frame. Reject diptychs, triptychs, before/after layouts, contact sheets, collages, split screens, side-by-side panels, and repeated full-image panels even when they show the same person or resemble a mirror reflection. A genuine mirror reflection exists inside one coherent shared scene; it is not a second full-frame panel with its own duplicated room and body. Set single_frame_image=false, collage_or_split_panel_detected=true, and repeated_subject_panel_detected=true when applicable. Also detect conspicuous unrequested held or foreground props such as a cup, sign, flower, or extra phone when they were not required by the request; ordinary room fixtures do not count. Schema:",
    "primary QA prompt collage contract",
)

qa = qa.replace(
    '"duplicate_subject_visible":false,"reflection_visible":false',
    '"duplicate_subject_visible":false,"single_frame_image":true,"collage_or_split_panel_detected":false,"repeated_subject_panel_detected":false,"unrequested_foreground_object_visible":false,"unrequested_foreground_object_labels":[],"reflection_visible":false',
)

qa = replace_once(
    qa,
    "Verify subject count, scene, framing, identity continuity, camera method and physical capture plausibility. For a casual selfie,",
    "Verify subject count, scene, framing, identity continuity, camera method and physical capture plausibility. Require one continuous photographic frame and reject any collage, diptych, split-screen, side-by-side repeated panel, contact sheet, or duplicated full-image layout. A coherent mirror reflection is not a separate panel. Detect conspicuous unrequested held or foreground props while ignoring ordinary room fixtures. For a casual selfie,",
    "compact QA prompt collage contract",
)

qa = replace_once(
    qa,
    "    required=['person_count','face_count','confidence','framing','framing_matches_request']\n",
    "    required=['person_count','face_count','confidence','framing','framing_matches_request','single_frame_image','collage_or_split_panel_detected','repeated_subject_panel_detected','unrequested_foreground_object_visible']\n",
    "required collage fields",
)

DETECTOR = r'''

def _pearson_correlation(xs: list[int], ys: list[int]) -> float:
    if len(xs) != len(ys) or not xs:
        return 0.0
    mean_x=sum(xs)/len(xs)
    mean_y=sum(ys)/len(ys)
    num=sum((x-mean_x)*(y-mean_y) for x,y in zip(xs,ys))
    den_x=math.sqrt(sum((x-mean_x)**2 for x in xs))
    den_y=math.sqrt(sum((y-mean_y)**2 for y in ys))
    if den_x <= 1e-9 or den_y <= 1e-9:
        return 0.0
    return max(-1.0, min(1.0, num/(den_x*den_y)))


def detect_split_panel_collage(image_bytes: bytes) -> dict:
    """Detect near-duplicate left/right panels before any fallible Vision review.

    This intentionally targets the recurring failure mode where a provider emits
    two almost-identical full photographs side by side. It requires all three:
    strong direct panel correlation, low mean absolute difference, and a strong
    vertical separator near the center. A normal mirror reflection inside one
    continuous scene should not satisfy that combination.
    """
    diagnostics={
        'detected': False,
        'reason': None,
        'best_split_x': None,
        'panel_correlation': 0.0,
        'panel_mad': 1.0,
        'seam_strength': 0.0,
        'interior_edge_baseline': 0.0,
    }
    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            image=ImageOps.exif_transpose(opened).convert('L')
            if image.width < 256 or image.height < 256:
                diagnostics['reason']='image_too_small'
                return diagnostics
            sample=image.resize((128, 160), Image.Resampling.BILINEAR)
    except Exception:
        diagnostics['reason']='decode_failed'
        return diagnostics

    pixels=list(sample.getdata())
    width,height=sample.size

    def column_edge(x: int) -> float:
        if x <= 0 or x >= width:
            return 0.0
        return sum(abs(pixels[y*width+x]-pixels[y*width+x-1]) for y in range(height))/height

    best=None
    for split in range(int(width*0.46), int(width*0.54)+1):
        panel_width=min(split, width-split)
        left_box=(split-panel_width, 0, split, height)
        right_box=(split, 0, split+panel_width, height)
        left=list(sample.crop(left_box).resize((64,128), Image.Resampling.BILINEAR).getdata())
        right=list(sample.crop(right_box).resize((64,128), Image.Resampling.BILINEAR).getdata())
        corr=_pearson_correlation(left,right)
        mad=sum(abs(a-b) for a,b in zip(left,right))/(len(left)*255.0)
        seam=column_edge(split)
        nearby=[column_edge(x) for x in range(max(1,split-24), min(width,split+25)) if abs(x-split) >= 5]
        baseline=statistics.median(nearby) if nearby else 0.0
        seam_ratio=seam/max(1.0,baseline)
        score=max(0.0,corr)*(1.0-mad)*min(1.0,seam_ratio/4.0)
        candidate=(score,split,corr,mad,seam,baseline,seam_ratio)
        if best is None or candidate[0] > best[0]:
            best=candidate

    if best is None:
        diagnostics['reason']='no_candidate_split'
        return diagnostics
    score,split,corr,mad,seam,baseline,seam_ratio=best
    detected=bool(corr >= 0.76 and mad <= 0.14 and seam >= 18.0 and seam_ratio >= 3.2)
    diagnostics.update({
        'detected': detected,
        'reason': 'near_duplicate_split_panels' if detected else 'threshold_not_met',
        'best_split_x': split,
        'panel_correlation': round(corr,4),
        'panel_mad': round(mad,4),
        'seam_strength': round(seam,4),
        'interior_edge_baseline': round(baseline,4),
        'seam_ratio': round(seam_ratio,4),
        'score': round(score,4),
    })
    return diagnostics
'''
qa = replace_once(qa, "\ndef _bool(v):\n", DETECTOR + "\n\ndef _bool(v):\n", "deterministic split-panel detector")

qa = replace_once(
    qa,
    "    near_duplicate=_bool(payload.get('near_duplicate_composition')) or (previous_metadata and previous_metadata.get('seed_family') == payload.get('seed_family') and previous_metadata.get('framing') == payload.get('framing') and previous_metadata.get('camera') == payload.get('camera'))\n",
    "    near_duplicate=_bool(payload.get('near_duplicate_composition')) or (previous_metadata and previous_metadata.get('seed_family') == payload.get('seed_family') and previous_metadata.get('framing') == payload.get('framing') and previous_metadata.get('camera') == payload.get('camera'))\n    single_frame=None if payload.get('single_frame_image') is None else _bool(payload.get('single_frame_image'))\n    collage_detected=_bool(payload.get('collage_or_split_panel_detected'))\n    repeated_panel_detected=_bool(payload.get('repeated_subject_panel_detected'))\n    unrequested_foreground_object=_bool(payload.get('unrequested_foreground_object_visible'))\n    unrequested_foreground_labels=[str(x) for x in (payload.get('unrequested_foreground_object_labels') or []) if str(x).strip()]\n    if single_frame is not True or collage_detected: codes.extend(['collage_or_split_panel','multiple_frames'])\n    if repeated_panel_detected: codes.append('repeated_subject_panel')\n    if unrequested_foreground_object: codes.append('unrequested_foreground_object')\n",
    "payload collage evaluation",
)

qa = replace_once(
    qa,
    "    result.visible_held_phone_detected=visible_held_phone\n",
    "    result.visible_held_phone_detected=visible_held_phone\n    result.single_frame_image=single_frame\n    result.collage_or_split_panel_detected=collage_detected\n    result.repeated_subject_panel_detected=repeated_panel_detected\n    result.unrequested_foreground_object_visible=unrequested_foreground_object\n    result.unrequested_foreground_object_labels=unrequested_foreground_labels\n",
    "result collage metadata",
)

qa = replace_once(
    qa,
    "async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:\n    settings=get_settings()\n",
    "async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:\n    split_diagnostics=detect_split_panel_collage(image_bytes)\n    if split_diagnostics.get('detected'):\n        result=GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=True, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='high', reason_codes=['collage_or_split_panel','multiple_frames','repeated_subject_panel'], model='deterministic_split_panel_detector')\n        result.single_frame_image=False\n        result.collage_or_split_panel_detected=True\n        result.repeated_subject_panel_detected=True\n        result.deterministic_split_panel_diagnostics=split_diagnostics\n        logger.info('IMAGE_SPLIT_PANEL_COLLAGE_REJECTED artifact_checksum_prefix=%s diagnostics=%s', hashlib.sha256(image_bytes).hexdigest()[:12], split_diagnostics)\n        return result\n    settings=get_settings()\n",
    "detector preflight",
)

qa = replace_once(
    qa,
    "    if codes & {'anatomy_profile_missing'}:\n",
    "    if codes & {'collage_or_split_panel','multiple_frames','repeated_subject_panel'}:\n        msg='این بار عکس دو تکه یا کلاژ شد؛ نفرستادمش و سکه‌ات برگشت.'\n    elif codes & {'unrequested_foreground_object'}:\n        msg='این بار یک وسیلهٔ اضافه و ناخواسته داخل عکس افتاد؛ نفرستادمش و سکه‌ات برگشت.'\n    elif codes & {'anatomy_profile_missing'}:\n",
    "user-facing collage message",
)

qa = replace_once(
    qa,
    "    if codes & {'framing_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden'}:\n",
    "    if codes & {'collage_or_split_panel','multiple_frames','repeated_subject_panel'}:\n        lines.append('Render exactly one continuous photograph in one frame. No collage, diptych, split screen, side-by-side panels, before-and-after layout, contact sheet, repeated room, or duplicated full-body panel. A mirror reflection must remain physically inside the same coherent scene.')\n    if codes & {'unrequested_foreground_object'}:\n        lines.append('Remove every conspicuous unrequested held or foreground prop. Do not invent a cup, flower, sign, extra phone, or other accessory unless it was explicitly requested.')\n    if codes & {'framing_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden'}:\n",
    "corrective collage prompt",
)

qa_path.write_text(qa, encoding="utf-8")

pipeline_path=Path("app/services/image_pipeline_v2.py")
pipeline=pipeline_path.read_text(encoding="utf-8")
pipeline=replace_once(
    pipeline,
    "    common=['collage','watermark','text','logo','plastic skin'",
    "    common=['collage','diptych','triptych','split screen','side-by-side panels','before and after layout','contact sheet','duplicated full-image panel','repeated room panel','watermark','text','logo','plastic skin'",
    "negative prompt split-panel terms",
)
pipeline_path.write_text(pipeline, encoding="utf-8")


test_path=Path("tests/test_split_panel_collage_rejection.py")
test_path.write_text(r'''from io import BytesIO

from PIL import Image, ImageDraw

from app.services.generated_image_qa_service import (
    QA_PROMPT,
    _qa_payload_missing_required_fields,
    corrective_prompt_for_reasons,
    detect_split_panel_collage,
    evaluate_generated_image_composition_payload,
)


def _png(image: Image.Image) -> bytes:
    output=BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _panel_image() -> Image.Image:
    half=Image.new("RGB", (256, 640), (205, 198, 186))
    draw=ImageDraw.Draw(half)
    draw.rectangle((16, 0, 42, 640), fill=(238, 233, 222))
    draw.rectangle((215, 0, 255, 640), fill=(181, 169, 151))
    draw.ellipse((88, 55, 168, 145), fill=(98, 71, 56))
    draw.rectangle((93, 140, 163, 390), fill=(151, 112, 91))
    draw.rectangle((99, 385, 125, 585), fill=(131, 96, 78))
    draw.rectangle((136, 385, 162, 585), fill=(131, 96, 78))
    canvas=Image.new("RGB", (518, 640), (245, 245, 245))
    canvas.paste(half, (0,0))
    right=half.copy()
    right_draw=ImageDraw.Draw(right)
    right_draw.ellipse((168, 180, 220, 225), fill=(240, 240, 235))
    canvas.paste(right, (262,0))
    return canvas


def _continuous_image() -> Image.Image:
    image=Image.new("RGB", (518, 640))
    draw=ImageDraw.Draw(image)
    for y in range(640):
        draw.line((0,y,517,y), fill=(40+(y%90), 90+(y%70), 130+(y%50)))
    draw.ellipse((56,70,220,300), fill=(220,170,130))
    draw.rectangle((290,120,490,580), fill=(72,105,145))
    return image


def _passing_payload() -> dict:
    return {
        "person_count": 1,
        "face_count": 1,
        "intended_subject_count": 1,
        "unexpected_additional_person_visible": False,
        "background_extra_person_visible": False,
        "duplicate_subject_visible": False,
        "single_frame_image": True,
        "collage_or_split_panel_detected": False,
        "repeated_subject_panel_detected": False,
        "unrequested_foreground_object_visible": False,
        "unrequested_foreground_object_labels": [],
        "reflection_visible": True,
        "reflection_matches_primary_subject": True,
        "reflected_distinct_person_visible": False,
        "selfie_detected": False,
        "mirror_selfie_detected": True,
        "confidence": "high",
        "framing": "full_body",
        "framing_matches_request": True,
        "head_inside_frame": True,
        "feet_inside_frame": True,
        "body_not_cropped": True,
        "requested_nudity_visible": True,
        "requested_scene_visible": True,
        "identity_consistency_reasonable": True,
        "camera_mode_matches_request": True,
        "camera_source_geometry_consistent": True,
        "third_person_viewpoint_detected": False,
        "natural_capture_plausible": True,
        "looks_like_id_photo": False,
        "reason_codes": [],
    }


def _requirements() -> dict:
    return {
        "explicit_nudity_requested": True,
        "full_body_visible": True,
        "framing_requirement": "full_body",
        "environment_visibility_required": True,
        "photo_contract": {
            "camera_mode": "mirror_selfie",
            "natural_capture_required": True,
            "identity_consistency_required": True,
            "identity_visibility_scope": "full",
        },
    }


def test_deterministic_detector_rejects_near_duplicate_side_by_side_panels():
    diagnostics=detect_split_panel_collage(_png(_panel_image()))
    assert diagnostics["detected"] is True
    assert diagnostics["panel_correlation"] >= 0.76
    assert diagnostics["panel_mad"] <= 0.14
    assert diagnostics["seam_ratio"] >= 3.2


def test_deterministic_detector_allows_single_continuous_asymmetric_photo():
    diagnostics=detect_split_panel_collage(_png(_continuous_image()))
    assert diagnostics["detected"] is False


def test_payload_rejects_collage_even_when_same_subject_mirror_reflection_claimed():
    payload=_passing_payload()
    payload.update({
        "single_frame_image": False,
        "collage_or_split_panel_detected": True,
        "repeated_subject_panel_detected": True,
    })
    result=evaluate_generated_image_composition_payload(
        payload,
        expected_subject_count=1,
        selfie_allowed=True,
        mirror_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "collage_or_split_panel" in result.reason_codes
    assert "multiple_frames" in result.reason_codes
    assert "repeated_subject_panel" in result.reason_codes


def test_payload_rejects_unrequested_foreground_prop():
    payload=_passing_payload()
    payload.update({
        "unrequested_foreground_object_visible": True,
        "unrequested_foreground_object_labels": ["mug"],
    })
    result=evaluate_generated_image_composition_payload(
        payload,
        expected_subject_count=1,
        selfie_allowed=True,
        mirror_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "unrequested_foreground_object" in result.reason_codes
    assert result.unrequested_foreground_object_labels == ["mug"]


def test_collage_fields_are_required_from_every_vision_reviewer():
    payload=_passing_payload()
    payload.pop("single_frame_image")
    payload.pop("collage_or_split_panel_detected")
    missing=_qa_payload_missing_required_fields(payload, _requirements())
    assert "single_frame_image" in missing
    assert "collage_or_split_panel_detected" in missing


def test_prompts_explicitly_distinguish_coherent_mirror_from_split_panel():
    assert "one continuous photographic frame" in QA_PROMPT
    assert "not a second full-frame panel" in QA_PROMPT
    correction=corrective_prompt_for_reasons(
        ["collage_or_split_panel", "unrequested_foreground_object"],
        identity_requirements={"face": "stable fictional face"},
        photo_contract={"camera_mode": "mirror_selfie"},
    ).lower()
    assert "exactly one continuous photograph" in correction
    assert "no collage" in correction
    assert "remove every conspicuous unrequested" in correction
    assert "cup" in correction
''', encoding="utf-8")
