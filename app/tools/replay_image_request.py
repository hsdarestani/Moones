from __future__ import annotations
import argparse, json
from dataclasses import asdict
from app.db.session import SessionLocal
from app.models.image_generation import ImageGenerationJob
from app.models.user import User
from app.services.image_prompt_engine import ensure_visual_profile
from app.services import image_pipeline_v2 as v2

def main():
    ap=argparse.ArgumentParser(description='Read-only replay of Image Pipeline v2 planning. No billing, provider, or Telegram side effects.')
    ap.add_argument('--job-id', type=int, required=True)
    args=ap.parse_args()
    db=SessionLocal()
    try:
        job=db.get(ImageGenerationJob, args.job_id)
        if not job: raise SystemExit('job_not_found')
        user=db.get(User, job.user_id)
        source=db.get(ImageGenerationJob, job.source_image_job_id) if job.source_image_job_id else None
        norm=v2.normalize_request_v2(job.user_request or '', user_id=job.user_id, chat_id=job.chat_id, source_message_id=job.source_telegram_message_id)
        intent=v2.parse_image_intent(norm)
        prev=v2.deserialize_resolved_plan(source.resolved_plan_json if source else None)
        merged=v2.merge_image_intent(intent, prev)
        profile=v2.ensure_visual_profile_v2(db, user, ensure_visual_profile(db, user))
        safety=v2.evaluate_safety_policy(intent)
        plan=v2.construct_resolved_plan(intent, merged, safety, profile, source_job=source, message_id=job.source_telegram_message_id, user_request=job.user_request or '')
        plan_errors=v2.validate_plan_invariants(plan, source_job=source, user_id=job.user_id, chat_id=job.chat_id)
        compiled=v2.compile_image_prompt(plan)
        prompt_errors=v2.validate_compiled_prompt(plan, compiled)
        stored=job.resolved_plan_json or (job.metadata_json or {}).get('resolved_plan') or {}
        recomputed=v2.plan_to_json(plan)
        keys=sorted(set(stored) | set(recomputed))
        diff={k:{'stored':stored.get(k), 'recomputed':recomputed.get(k)} for k in keys if stored.get(k) != recomputed.get(k)}
        db.rollback()
        print(json.dumps({'job_id':job.id,'stored_plan_version':stored.get('plan_version'),'plan_errors':plan_errors,'prompt_errors':prompt_errors,'diff':diff,'compiled_provider_parameters':compiled.provider_parameters,'side_effects':'none'}, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()
if __name__ == '__main__': main()
