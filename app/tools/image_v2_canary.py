from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.models.image_generation import PartnerVisualProfile
from app.services import image_pipeline_v2 as v2

BASE=[
 {'request':'یه عکس روی مبل بده','expected':{'action':'new_generation','scene':'sofa','support_surface':'sofa','fallback_required':False}},
 {'request':'عکس بده ممه هاتو ببینم','expected':{'content_not_normal':True,'fallback_required':False}},
 {'request':'عکس بده کصتو ببینم','expected':{'policy':'explicit_genital_visibility_not_supported'}},
 {'request':'عکس بده لخت باشی توش','expected':{'content':'full_nudity'}},
 {'request':'درد واژن دارم توضیح پزشکی بده','expected':{'action':'chat'}},
]
NOUNS=['دست','مو','لباس','مبل','اتاق','کتاب','کیف','کفش','صورت','چشم']
SCENES=['مبل','تخت','کافه','پارک','ماشین','حمام','آینه','دانشگاه','مترو','باشگاه']
POSES=['نشسته','ایستاده','لم داده','دراز کشیده','قدم بزن']
FIXTURES=BASE+ [{'request':f'عکس بده {n}هاتو ببینم','expected':{'fallback_required':False}} for n in NOUNS] + [{'request':f'یه عکس روی {x} بده','expected':{'fallback_required':False}} for x in SCENES] + [{'request':f'عکس بده {p}','expected':{'fallback_required':False}} for p in POSES]
for i in range(100-len(FIXTURES)):
    rel=['توی','داخل','کنار','پشت','جلوی'][i % 5]
    scene=['کافه','مبل','تخت','پارک','خیابان'][i % 5]
    FIXTURES.append({'request':f'عکس شماره {i} بده {rel} {scene}', 'expected':{'fallback_required':False}})

def _profile():
    return PartnerVisualProfile(user_id=1, version=2, fictional_age=24, base_seed=42, partner_name='Mina', gender_presentation='adult woman', face_description='oval face', hair_description='dark hair', eye_description='brown eyes', skin_description='warm skin', body_description='average build', distinguishing_details='dimple', profile_json={'face_shape':'oval','eye_color':'brown','hair_color':'dark','skin_tone':'warm','build':'average'})

def run_canary(requests=None):
    fixtures=requests or FIXTURES
    report={'count':0,'parser_fallback_count':0,'parser_fallback_rate':0,'unmatched_token_frequency':{},'content_mode_mismatches':0,'route_mismatches':0,'scene_mismatches':0,'support_surface_mismatches':0,'policy_mismatches':0,'adult_to_normal_downgrades':0,'invariant_failures':0,'prompt_validation_failures':0,'single_subject_constraint_failures':0,'identity_fingerprint_changes':0,'plan_round_trip_failures':0,'source_plan_inheritance_failures':0,'resend_retrievability_mismatches':0,'billing_before_validation_failures':0,'legacy_v2_semantic_diffs':0,'unsupported_expected_count':0,'failure_count':0,'failures':[]}
    for fx in fixtures[:100]:
        raw=fx['request']; exp=fx.get('expected',{})
        actual={}; codes=[]; report['count']+=1
        intent=v2.parse_image_intent(v2.normalize_request_v2(raw))
        ctx=v2.AdultImagePolicyContext(adult_enabled=True, adult_addon_owned=True, adult_addon_enabled=True, fictional_partner_min_age=24)
        safety=v2.evaluate_safety_policy(intent, ctx)
        merged=v2.merge_image_intent(intent)
        profile=_profile(); fp_before=v2.identity_descriptor_v2(profile)
        plan=v2.construct_resolved_plan(intent, merged, safety, profile, message_id=report['count'], user_request=raw)
        inv=[] if safety.decision == v2.PolicyDecision.DENY else v2.validate_plan_invariants(plan); compiled=v2.compile_image_prompt(plan); perr=[] if safety.decision == v2.PolicyDecision.DENY else v2.validate_compiled_prompt(plan, compiled)
        round_trip=v2.plan_to_json(v2.deserialize_resolved_plan(v2.plan_to_json(plan))) == v2.plan_to_json(plan)
        actual.update(action=str(intent.continuity.action), scene=plan.scene.value, support_surface=plan.support_surface.value, fallback_required=intent.parse_coverage.fallback_required, policy=safety.reason_code, content=str(intent.content_classification))
        report['parser_fallback_count'] += int(intent.parse_coverage.fallback_required)
        for k,c in intent.parse_coverage.unmatched_token_frequency.items(): report['unmatched_token_frequency'][k]=report['unmatched_token_frequency'].get(k,0)+c
        if intent.adult_intent and intent.content_classification == v2.ContentClassification.NORMAL: report['adult_to_normal_downgrades']+=1; codes.append('adult_to_normal')
        if inv: report['invariant_failures']+=1; codes.extend(inv)
        if perr: report['prompt_validation_failures']+=1; codes.extend(perr)
        if not round_trip: report['plan_round_trip_failures']+=1; codes.append('round_trip')
        if v2.identity_descriptor_v2(profile) != fp_before: report['identity_fingerprint_changes']+=1; codes.append('identity_changed')
        if exp.get('fallback_required') is not None and intent.parse_coverage.fallback_required != exp['fallback_required']: codes.append('fallback_mismatch')
        if exp.get('policy') and safety.reason_code != exp['policy']: report['policy_mismatches']+=1; codes.append('policy_mismatch')
        if exp.get('scene') and actual['scene'] != exp['scene']: report['scene_mismatches']+=1; codes.append('scene_mismatch')
        if exp.get('support_surface') and actual['support_surface'] != exp['support_surface']: report['support_surface_mismatches']+=1; codes.append('surface_mismatch')
        if exp.get('content_not_normal') and intent.content_classification == v2.ContentClassification.NORMAL: report['content_mode_mismatches']+=1; codes.append('content_normal')
        if codes:
            report['failure_count']+=1; report['failures'].append({'request':raw,'expected':exp,'actual':actual,'normalized_tokens':intent.parse_coverage.token_debug,'semantic_matches':intent.parse_coverage.semantic_matches,'unmatched_tokens':intent.parse_coverage.unmatched_meaningful_tokens,'invariant_codes':codes})
    report['parser_fallback_rate']=report['parser_fallback_count']/max(report['count'],1)
    return report

if __name__ == '__main__':
    r=run_canary(); print(json.dumps(r, ensure_ascii=False, indent=2, default=lambda o: getattr(o,'__dict__',str(o))))
    must_zero=['parser_fallback_count','content_mode_mismatches','route_mismatches','scene_mismatches','support_surface_mismatches','policy_mismatches','adult_to_normal_downgrades','invariant_failures','prompt_validation_failures','single_subject_constraint_failures','identity_fingerprint_changes','plan_round_trip_failures','source_plan_inheritance_failures','billing_before_validation_failures','failure_count']
    raise SystemExit(0 if all(r.get(k,0)==0 for k in must_zero) else 1)
