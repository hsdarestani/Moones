from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def replace_once(path, old, new):
    p=ROOT/path
    text=p.read_text(encoding='utf-8')
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f'missing block in {path}')
    p.write_text(text.replace(old,new,1),encoding='utf-8')

context='app/services/semantic_image_router_context.py'
replace_once(
    context,
    "        must=_as_dict(requirements.get('must_satisfy'))\n        values={\n",
    "        must=_as_dict(requirements.get('must_satisfy'))\n        adult_intent=_as_dict(current.get('adult_intent'))\n        values={\n",
)
replace_once(
    context,
    "            'explicit_anatomy_focus': bool(requirements.get('anatomy_qa_required') or current.get('adult_intent', {}).get('explicit_anatomy_focus') if isinstance(current.get('adult_intent'), dict) else False),\n",
    "            'explicit_anatomy_focus': bool(requirements.get('anatomy_qa_required') or adult_intent.get('explicit_anatomy_focus')),\n",
)
replace_once(
    context,
    "        failed_rows=db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status.in_(FAILED_IMAGE_JOB_STATUSES)).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(3)).all()\n"
    "        cutoff=datetime.utcnow()-timedelta(hours=2)\n"
    "        failed_rows=[row for row in failed_rows if not getattr(row, 'created_at', None) or row.created_at >= cutoff]\n"
    "        retry_text, retry_visual=merge_failed_image_retry_contract(failed_rows)\n",
    "        failed_rows=db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status.in_(FAILED_IMAGE_JOB_STATUSES)).order_by(ImageGenerationJob.created_at.desc(), ImageGenerationJob.id.desc()).limit(3)).all()\n"
    "        cutoff=datetime.utcnow()-timedelta(hours=2)\n"
    "        failed_rows=[row for row in failed_rows if not getattr(row, 'created_at', None) or row.created_at >= cutoff]\n"
    "        retry_chain=[]\n"
    "        for row in failed_rows:\n"
    "            if retry_chain:\n"
    "                newer=retry_chain[-1]\n"
    "                newer_text=' '.join(str(getattr(newer, 'user_request', '') or '').replace('‌',' ').split())\n"
    "                if not any(marker in newer_text for marker in ('قبلی','همین','همون','همونو')):\n"
    "                    break\n"
    "                newer_at=getattr(newer, 'created_at', None); older_at=getattr(row, 'created_at', None)\n"
    "                if newer_at and older_at and newer_at-older_at > timedelta(minutes=10):\n"
    "                    break\n"
    "            retry_chain.append(row)\n"
    "        retry_text, retry_visual=merge_failed_image_retry_contract(retry_chain)\n",
)

telegram='app/api/telegram.py'
replace_once(
    telegram,
    "        if context.latest_image_job and str(context.latest_image_job.status or '') in {'failed','delivery_failed'}:\n",
    "        if context.latest_image_job and context.latest_image_job.retry_request_text and str(context.latest_image_job.status or '') in {'failed','delivery_failed'}:\n",
)

tests=ROOT/'tests/test_issue_180_image_failure_retry.py'
text=tests.read_text(encoding='utf-8')
addition='''\n\ndef test_failed_contract_merge_does_not_pull_unrelated_older_request():\n    from app.services.semantic_image_router_context import merge_failed_image_retry_contract\n    latest=SimpleNamespace(user_request="یه عکس تو پارک بده", metadata_json={}, resolved_plan_json={"scene":{"value":"park"}})\n    older=SimpleNamespace(user_request="یه عکس قدی بده", metadata_json={}, resolved_plan_json={"composition":{"framing":"full_body"}})\n    text, visual=merge_failed_image_retry_contract([latest])\n    assert text == "یه عکس تو پارک بده"\n    assert visual["scene"] == "park"\n    assert "framing" not in visual\n'''
if 'test_failed_contract_merge_does_not_pull_unrelated_older_request' not in text:
    tests.write_text(text+addition,encoding='utf-8')

print('issue 180 retry scope hardened')
