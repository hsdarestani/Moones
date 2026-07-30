from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"expected block missing in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Route relative previous-image requests to an actually sent source, and add
# deterministic retry metadata for a recent failed visual contract.
router = "app/services/semantic_image_intent_router.py"
replace_once(
    router,
    "    compact_user_visible_summary: str | None = None\n",
    "    compact_user_visible_summary: str | None = None\n"
    "    retry_request_text: str | None = None\n"
    "    retry_visual_intent: dict[str, Any] | None = None\n",
)
replace_once(
    router,
    "    safety_relevant_signals: dict[str, Any] = field(default_factory=dict)\n\n    def __post_init__(self) -> None:\n",
    "    safety_relevant_signals: dict[str, Any] = field(default_factory=dict)\n"
    "    retry_request_text: str | None = None\n"
    "    retry_visual_intent: dict[str, Any] | None = None\n\n"
    "    def __post_init__(self) -> None:\n",
)
replace_once(
    router,
    "    latest = context.recent_image_job or context.latest_image_job\n"
    "    if latest is None or latest.job_id is None or str(latest.status or \"\") != \"sent\":\n"
    "        return decision\n",
    "    latest = next((candidate for candidate in (context.recent_image_job, context.latest_image_job)\n"
    "                   if candidate is not None and candidate.job_id is not None and str(candidate.status or \"\") == \"sent\"), None)\n"
    "    if latest is None:\n"
    "        return decision\n",
)
replace_once(
    router,
    "\ndef enforce_partner_photo_defaults(\n",
    "\ndef enforce_recent_failed_image_retry(\n"
    "    context: SemanticImageRouterContext,\n"
    "    decision: SemanticImageDecision,\n"
    ") -> SemanticImageDecision:\n"
    "    \"\"\"Retry the complete recent failed image contract for a short generic delivery command.\n\n"
    "    This runs after normal semantic classification. It never turns ordinary chat into an\n"
    "    image request: the current message must itself be an explicit, short photo delivery\n"
    "    command, and the newest job must have failed recently with a preserved contract.\n"
    "    \"\"\"\n"
    "    normalized = _norm_intent_text(context.current_user_message)\n"
    "    words = normalized.split()\n"
    "    image_surface = any(marker in normalized for marker in (\"عکس\", \"تصویر\", \"سلفی\"))\n"
    "    delivery_surface = any(marker in normalized for marker in (\"بده\", \"بدی\", \"بفرست\", \"بفرس\", \"دوباره\"))\n"
    "    generic_retry = bool(image_surface and delivery_surface and len(words) <= 5 and not any(\n"
    "        marker in normalized for marker in (\"قبلی\", \"همین\", \"همون\", \"کافه\", \"خونه\", \"خانه\", \"خیابون\", \"خیابان\", \"پارک\", \"ماشین\", \"لباس\", \"لخت\", \"قدی\", \"تمام قد\", \"نشسته\", \"ایستاده\", \"زاویه\", \"نور\")\n"
    "    ))\n"
    "    if not generic_retry:\n"
    "        return decision\n"
    "    latest = context.latest_image_job\n"
    "    if latest is None or str(latest.status or \"\") not in {\"failed\", \"delivery_failed\"}:\n"
    "        return decision\n"
    "    timestamp = latest.failed_at or latest.created_at\n"
    "    try:\n"
    "        failed_at = datetime.fromisoformat(str(timestamp))\n"
    "        if failed_at.tzinfo is not None:\n"
    "            failed_at = failed_at.replace(tzinfo=None)\n"
    "        if datetime.utcnow() - failed_at > timedelta(hours=2):\n"
    "            return decision\n"
    "    except Exception:\n"
    "        return decision\n"
    "    if not latest.retry_request_text or not latest.retry_visual_intent:\n"
    "        return decision\n"
    "    decision.action = SemanticImageAction.GENERATE_NEW\n"
    "    decision.media_delivery_requested = True\n"
    "    decision.needs_clarification = False\n"
    "    decision.source_reference = None\n"
    "    decision.reason_code = \"recent_failed_image_contract_retry\"\n"
    "    decision.retry_request_text = latest.retry_request_text\n"
    "    decision.retry_visual_intent = dict(latest.retry_visual_intent)\n"
    "    decision.visual_intent = VisualIntent(**decision.retry_visual_intent)\n"
    "    logger.info(\"IMAGE_FAILED_CONTRACT_RETRY_LOCKED failed_job_id=%s framing=%s scene=%s\", latest.job_id, decision.visual_intent.framing, decision.visual_intent.scene)\n"
    "    return decision\n\n\n"
    "def enforce_partner_photo_defaults(\n",
)

# 2) Build a cumulative retry contract from the newest failed jobs. Older explicit
# framing/body requirements survive while newer scene changes override them.
context_file = "app/services/semantic_image_router_context.py"
replace_once(
    context_file,
    "from datetime import datetime\n",
    "from datetime import datetime, timedelta\nimport json\n",
)
replace_once(
    context_file,
    "ACTIVE_IMAGE_JOB_STATUSES = {\"queued\", \"processing\", \"generating\", \"sending\", \"delivery_failed\"}\n\n",
    "ACTIVE_IMAGE_JOB_STATUSES = {\"queued\", \"processing\", \"generating\", \"sending\", \"delivery_failed\"}\n"
    "FAILED_IMAGE_JOB_STATUSES = {\"failed\", \"delivery_failed\"}\n\n\n"
    "def _as_dict(value):\n"
    "    if isinstance(value, dict):\n"
    "        return value\n"
    "    if isinstance(value, str) and value.strip():\n"
    "        try:\n"
    "            parsed=json.loads(value)\n"
    "            return parsed if isinstance(parsed, dict) else {}\n"
    "        except Exception:\n"
    "            return {}\n"
    "    return {}\n\n\n"
    "def _resolved_value(value):\n"
    "    return value.get('value') if isinstance(value, dict) and 'value' in value else value\n\n\n"
    "def merge_failed_image_retry_contract(jobs):\n"
    "    \"\"\"Return cumulative request text and a VisualIntent-compatible dictionary.\"\"\"\n"
    "    merged={}\n"
    "    requests=[]\n"
    "    list_fields={\"required_visible_environment_elements\", \"required_body_regions\", \"forbidden_body_regions\", \"freeform_visual_constraints\"}\n"
    "    for job in reversed(list(jobs or [])):\n"
    "        request=str(getattr(job, 'user_request', '') or '').strip()\n"
    "        if request and request not in requests:\n"
    "            requests.append(request)\n"
    "        metadata=_as_dict(getattr(job, 'metadata_json', None))\n"
    "        plan=_as_dict(getattr(job, 'resolved_plan_json', None)) or _as_dict(metadata.get('resolved_plan'))\n"
    "        current=_as_dict(plan.get('current_intent'))\n"
    "        composition=_as_dict(plan.get('composition'))\n"
    "        requirements=_as_dict(plan.get('visual_requirements')) or _as_dict(metadata.get('visual_requirements'))\n"
    "        contract=_as_dict(requirements.get('photo_contract')) or _as_dict(composition.get('photo_contract')) or _as_dict(metadata.get('photo_contract'))\n"
    "        body_visibility=_as_dict(plan.get('body_visibility')) or _as_dict(metadata.get('body_visibility'))\n"
    "        classification=str(current.get('content_classification') or metadata.get('content_classification') or '').lower()\n"
    "        nudity_level=None\n"
    "        for candidate in ('full_nudity','topless','lingerie','suggestive','normal'):\n"
    "            if candidate in classification:\n"
    "                nudity_level=candidate\n"
    "                break\n"
    "        required_regions=list(requirements.get('required_body_regions') or contract.get('required_body_regions') or [])\n"
    "        forbidden_regions=list(requirements.get('forbidden_body_regions') or contract.get('forbidden_body_regions') or [])\n"
    "        for name, region in body_visibility.items():\n"
    "            region=_as_dict(region)\n"
    "            if region.get('visibility_requested') or region.get('framing_requested'):\n"
    "                required_regions.append(name)\n"
    "            if region.get('visibility_negated'):\n"
    "                forbidden_regions.append(name)\n"
    "        if requirements.get('full_body_visible'):\n"
    "            required_regions.append('full_body')\n"
    "        must=_as_dict(requirements.get('must_satisfy'))\n"
    "        values={\n"
    "            'scene': _resolved_value(plan.get('scene')) or metadata.get('resolved_scene') or metadata.get('semantic_requested_scene'),\n"
    "            'location': _resolved_value(plan.get('location')) or metadata.get('resolved_location') or metadata.get('semantic_requested_location'),\n"
    "            'environment_type': _resolved_value(plan.get('environment_type')),\n"
    "            'privacy': _resolved_value(plan.get('privacy')),\n"
    "            'pose': _resolved_value(plan.get('pose')),\n"
    "            'activity': _resolved_value(plan.get('activity')),\n"
    "            'wardrobe': _resolved_value(plan.get('wardrobe')) or metadata.get('wardrobe_level'),\n"
    "            'camera_mode': contract.get('camera_mode'),\n"
    "            'framing': requirements.get('framing_requirement') or composition.get('framing') or metadata.get('resolved_requested_framing') or contract.get('framing'),\n"
    "            'partner_visible': contract.get('partner_visible'),\n"
    "            'face_visible': contract.get('face_visible'),\n"
    "            'face_hidden': contract.get('face_hidden'),\n"
    "            'back_to_camera': contract.get('back_to_camera'),\n"
    "            'primary_subject': contract.get('primary_subject') or 'partner',\n"
    "            'request_type': contract.get('request_type') or 'partner_photo',\n"
    "            'current_scene_from_chat': contract.get('current_scene_from_chat'),\n"
    "            'scene_context_summary': contract.get('scene_context_summary'),\n"
    "            'nudity_level': nudity_level,\n"
    "            'explicit_anatomy_focus': bool(requirements.get('anatomy_qa_required') or current.get('adult_intent', {}).get('explicit_anatomy_focus') if isinstance(current.get('adult_intent'), dict) else False),\n"
    "            'required_visible_environment_elements': list(contract.get('required_visible_environment_elements') or must.get('required_scene_elements') or []),\n"
    "            'required_body_regions': required_regions,\n"
    "            'forbidden_body_regions': forbidden_regions,\n"
    "            'freeform_visual_constraints': list(current.get('passthrough_visual_details') or []),\n"
    "        }\n"
    "        for key, value in values.items():\n"
    "            if value in (None, '', [], {}):\n"
    "                continue\n"
    "            if key in list_fields:\n"
    "                merged[key]=list(dict.fromkeys(list(merged.get(key) or []) + list(value)))\n"
    "            else:\n"
    "                merged[key]=value\n"
    "    return '؛ سپس '.join(requests), merged\n\n",
)
replace_once(
    context_file,
    "    active_summary=_job_summary(db, active)\n"
    "    latest_summary=_job_summary(db, latest)\n",
    "    active_summary=_job_summary(db, active)\n"
    "    latest_summary=_job_summary(db, latest)\n"
    "    if latest_summary and str(latest_summary.status or '') in FAILED_IMAGE_JOB_STATUSES:\n"
    "        failed_rows=db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status.in_(FAILED_IMAGE_JOB_STATUSES)).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(3)).all()\n"
    "        cutoff=datetime.utcnow()-timedelta(hours=2)\n"
    "        failed_rows=[row for row in failed_rows if not getattr(row, 'created_at', None) or row.created_at >= cutoff]\n"
    "        retry_text, retry_visual=merge_failed_image_retry_contract(failed_rows)\n"
    "        latest_summary.retry_request_text=retry_text or None\n"
    "        latest_summary.retry_visual_intent=retry_visual or None\n",
)

# 3) Wire deterministic retry before partner defaults and use the recovered request
# text when enqueuing. Mark ordinary chat after an image failure for grounding.
telegram = "app/api/telegram.py"
replace_once(
    telegram,
    "    enforce_referenced_object_request, enforce_relative_previous_image_reference, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n",
    "    enforce_referenced_object_request, enforce_relative_previous_image_reference, enforce_recent_failed_image_retry, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n",
)
replace_once(
    telegram,
    "        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)\n"
    "        semantic_decision = enforce_partner_photo_defaults(context, semantic_decision)\n",
    "        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)\n"
    "        semantic_decision = enforce_recent_failed_image_retry(context, semantic_decision)\n"
    "        semantic_decision = enforce_partner_photo_defaults(context, semantic_decision)\n",
)
replace_once(
    telegram,
    "          effective_request_text = pending_resolution.effective_request_text if pending_resolution and pending_resolution.effective_request_text else text\n",
    "          effective_request_text = semantic_decision.retry_request_text or (pending_resolution.effective_request_text if pending_resolution and pending_resolution.effective_request_text else text)\n",
)
replace_once(
    telegram,
    "          logger.info(\"IMAGE_REQUEST_NEVER_FELL_THROUGH_TO_CHAT user_id=%s action=%s\", user.id, route_decision.route)\n"
    "          return result\n"
    "        if settings.simple_chat_mode:\n",
    "          logger.info(\"IMAGE_REQUEST_NEVER_FELL_THROUGH_TO_CHAT user_id=%s action=%s\", user.id, route_decision.route)\n"
    "          return result\n"
    "        if context.latest_image_job and str(context.latest_image_job.status or '') in {'failed','delivery_failed'}:\n"
    "          message_metadata['image_job_grounding']={'status':context.latest_image_job.status,'error_code':context.latest_image_job.error_code,'job_id':context.latest_image_job.job_id}\n"
    "        if settings.simple_chat_mode:\n",
)

# 4) Ground ordinary persona chat after a failed image job so failed visual plans
# cannot become fake physical-state claims.
simple_chat = "app/engine/simple_chat.py"
replace_once(
    simple_chat,
    "def _is_abusive_or_threatening(text: str) -> bool:\n",
    "def failed_image_grounding_block(message_metadata: dict | None) -> str:\n"
    "    info=(message_metadata or {}).get('image_job_grounding') or {}\n"
    "    if str(info.get('status') or '') not in {'failed','delivery_failed'}:\n"
    "        return ''\n"
    "    return (\"[Failed image grounding] The most recent image request failed and no requested visual scene was delivered. \"\n"
    "            \"Treat the user's current turn as ordinary conversation unless it explicitly requests another image. \"\n"
    "            \"Never claim that the partner is currently wearing, exposing, posing, standing, sitting, lying, or located as described by that failed image request. \"\n"
    "            \"Do not convert an unfulfilled image prompt into a real-world physical-status statement.\")\n\n\n"
    "def _is_abusive_or_threatening(text: str) -> bool:\n",
)
replace_once(
    simple_chat,
    "    if reply_context is not None:\n"
    "        prompt += \"\\n\\n\" + reply_context.prompt_block()\n",
    "    if reply_context is not None:\n"
    "        prompt += \"\\n\\n\" + reply_context.prompt_block()\n"
    "    image_grounding=failed_image_grounding_block(message_metadata)\n"
    "    if image_grounding:\n"
    "        prompt += \"\\n\\n\" + image_grounding\n"
    "        logger.info(\"FAILED_IMAGE_CHAT_GROUNDING_APPLIED user_id=%s\", user.id)\n",
)

# 5) Exact provider-free regression tests for issue #180.
test_path = ROOT / "tests/test_issue_180_image_failure_retry.py"
test_path.write_text('''from datetime import datetime\nfrom types import SimpleNamespace\n\n\ndef _decision(action):\n    from app.services.semantic_image_intent_router import SemanticImageDecision\n    return SemanticImageDecision(action=action, media_delivery_requested=action != "chat", confidence=.9, reason_code="test")\n\n\ndef test_relative_previous_falls_back_to_actual_sent_job_when_latest_failed():\n    from app.services.semantic_image_intent_router import (\n        RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext,\n        enforce_relative_previous_image_reference,\n    )\n    context=SemanticImageRouterContext(\n        current_user_message="همین قبلی رو تو کافه بده",\n        recent_image_job=RecentImageJobSummary(job_id=41,status="sent"),\n        latest_image_job=RecentImageJobSummary(job_id=42,status="failed"),\n    )\n    result=enforce_relative_previous_image_reference(context,_decision(SemanticImageAction.GENERATE_NEW))\n    assert result.action == SemanticImageAction.REFINE_PREVIOUS\n    assert result.source_reference.job_id == 41\n\n\ndef test_failed_contract_merge_keeps_old_full_body_and_new_cafe_scene():\n    from app.services.semantic_image_router_context import merge_failed_image_retry_contract\n    old=SimpleNamespace(\n        user_request="یه عکس قدی بده",\n        metadata_json={},\n        resolved_plan_json={\n            "composition":{"framing":"full_body"},\n            "visual_requirements":{"framing_requirement":"full_body","full_body_visible":True,"required_body_regions":["full_body"],"photo_contract":{"camera_mode":"mirror_selfie","partner_visible":True}},\n            "scene":{"value":"home"},\n        },\n    )\n    new=SimpleNamespace(\n        user_request="همین قبلی رو تو کافه بده",\n        metadata_json={},\n        resolved_plan_json={\n            "scene":{"value":"cafe"},\n            "location":{"value":"cafe"},\n            "visual_requirements":{"photo_contract":{"partner_visible":True}},\n        },\n    )\n    text, visual=merge_failed_image_retry_contract([new, old])\n    assert text == "یه عکس قدی بده؛ سپس همین قبلی رو تو کافه بده"\n    assert visual["scene"] == "cafe"\n    assert visual["framing"] == "full_body"\n    assert "full_body" in visual["required_body_regions"]\n    assert visual["camera_mode"] == "mirror_selfie"\n\n\ndef test_short_photo_command_retries_recent_failed_contract_exactly():\n    from app.services.semantic_image_intent_router import (\n        RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext,\n        enforce_recent_failed_image_retry,\n    )\n    context=SemanticImageRouterContext(\n        current_user_message="عکس بده",\n        latest_image_job=RecentImageJobSummary(\n            job_id=52, status="failed", failed_at=datetime.utcnow().isoformat(),\n            retry_request_text="یه عکس قدی بده؛ سپس همین قبلی رو تو کافه بده",\n            retry_visual_intent={"scene":"cafe","location":"cafe","framing":"full_body","camera_mode":"mirror_selfie","required_body_regions":["full_body"]},\n        ),\n    )\n    result=enforce_recent_failed_image_retry(context,_decision(SemanticImageAction.GENERATE_NEW))\n    assert result.action == SemanticImageAction.GENERATE_NEW\n    assert result.reason_code == "recent_failed_image_contract_retry"\n    assert result.retry_request_text.startswith("یه عکس قدی بده")\n    assert result.visual_intent.scene == "cafe"\n    assert result.visual_intent.framing == "full_body"\n\n\ndef test_ordinary_chat_after_failure_gets_non_hallucination_grounding():\n    from app.engine.simple_chat import failed_image_grounding_block\n    block=failed_image_grounding_block({"image_job_grounding":{"status":"failed","job_id":52}})\n    assert "most recent image request failed" in block\n    assert "Never claim" in block\n    assert failed_image_grounding_block({}) == ""\n''', encoding="utf-8")

print("issue #180 product patch applied")
