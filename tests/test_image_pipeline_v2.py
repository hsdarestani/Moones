from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact, PartnerVisualProfile
from app.services.persian_normalization import normalize_and_tokenize
from app.services import image_pipeline_v2 as v2


def test_persian_suffix_tokenization_arbitrary_nouns():
    text='دستت موهات لباست مبلش اتاقمون واژنت باسنت سینه‌هات'
    toks=normalize_and_tokenize(text).tokens
    stems=[t.stem for t in toks]
    assert stems == ['دست','مو','لباس','مبل','اتاق','واژن','باسن','سینه']
    assert all(t.start < t.end for t in toks)
    assert toks[1].suffixes == ['ها','هات']


def test_negated_visibility_and_nonvisual_context():
    neg=v2.parse_image_intent(v2.normalize_request_v2('واژنت معلوم نباشه'))
    assert neg.body_visibility.regions['genitals'].visibility_negated
    med=v2.parse_image_intent(v2.normalize_request_v2('درد واژن دارم توضیح بده'))
    assert not med.body_visibility.regions['genitals'].visibility_requested
    assert not med.is_image_request


def test_scene_support_pose_prompt_consistency():
    req=v2.normalize_request_v2('یه عکس روی مبل لم داده بفرست')
    intent=v2.parse_image_intent(req)
    merged=v2.merge_image_intent(intent)
    profile=PartnerVisualProfile(user_id=1, version=2, fictional_age=24, base_seed=42, profile_json={'face_shape':'oval','jaw':'soft jaw','eye_shape':'almond','eye_color':'brown','eyebrow_shape':'arched','hair_texture':'wavy','hair_color':'brown','skin_tone':'warm','feature':'dimple','build':'average','height':'average'})
    plan=v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), profile, message_id=10, user_request=req.raw_text)
    assert not v2.validate_plan_invariants(plan)
    compiled=v2.compile_image_prompt(plan)
    assert 'sofa' in compiled.positive_prompt
    assert 'bed' in compiled.negative_prompt
    assert not v2.validate_compiled_prompt(plan, compiled)


def test_variation_seed_differs_from_source_and_inherits_plan():
    src=v2.ResolvedImagePlan(scene=v2.ResolvedField('cafe', v2.Provenance.EXPLICIT), support_surface=v2.ResolvedField('chair', v2.Provenance.EXPLICIT), pose=v2.ResolvedField('seated', v2.Provenance.EXPLICIT))
    intent=v2.parse_image_intent(v2.normalize_request_v2('یکی دیگه مثل قبلی'))
    merged=v2.merge_image_intent(intent, src)
    profile=PartnerVisualProfile(user_id=1, version=2, fictional_age=24, base_seed=100, profile_json={})
    source_job=ImageGenerationJob(id=9, idempotency_key='s', correlation_id='s', user_id=1, chat_id=2, status='sent', seed=123, sent_at=datetime.utcnow())
    plan=v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), profile, source_job=source_job, message_id=11, user_request='یکی دیگه')
    assert plan.scene.value == 'cafe'
    assert plan.seed_strategy['final_provider_seed'] != 123


def test_source_lookup_same_chat_ttl_and_artifact():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, ImageGenerationJob.__table__, ImageGenerationArtifact.__table__])
    s=sessionmaker(bind=e)(); u=User(telegram_id=1); s.add(u); s.flush()
    good=ImageGenerationJob(idempotency_key='g', correlation_id='g', user_id=u.id, chat_id=10, status='sent', sent_at=datetime.utcnow())
    stale=ImageGenerationJob(idempotency_key='x', correlation_id='x', user_id=u.id, chat_id=10, status='sent', sent_at=datetime.utcnow()-timedelta(hours=2))
    other=ImageGenerationJob(idempotency_key='o', correlation_id='o', user_id=u.id, chat_id=11, status='sent', sent_at=datetime.utcnow())
    s.add_all([good, stale, other]); s.flush()
    s.add(ImageGenerationArtifact(job_id=good.id, mime_type='image/png', checksum='c', byte_size=1, image_bytes=b'a'))
    s.add(ImageGenerationArtifact(job_id=other.id, mime_type='image/png', checksum='d', byte_size=1, image_bytes=b'b'))
    s.commit()
    assert v2.find_eligible_source_image_context(s, user_id=u.id, chat_id=10).id == good.id
    assert v2.find_eligible_source_image_context(s, user_id=u.id, chat_id=11).id == other.id


def test_production_regression_adult_persian_fixtures():
    breast=v2.parse_image_intent(v2.normalize_request_v2('عکس بده ممه هاتو ببینم'))
    assert 'breasts' in breast.body_visibility.regions
    assert breast.body_visibility.regions['breasts'].visibility_requested
    assert breast.content_classification != v2.ContentClassification.NORMAL
    genital=v2.parse_image_intent(v2.normalize_request_v2('عکس بده کصتو ببینم'))
    assert 'genitals' in genital.body_visibility.regions
    assert v2.evaluate_safety_policy(genital).reason_code == 'explicit_genital_visibility_not_supported'
    nude=v2.parse_image_intent(v2.normalize_request_v2('عکس بده لخت باشی توش'))
    assert nude.content_classification == v2.ContentClassification.FULL_NUDITY


def test_morphology_matrix_suffixes_and_nonvisual():
    for word, region in [('ممه‌هاتو','breasts'),('سینمو','breasts'),('واژنتو','genitals'),('کونشو','buttocks'),('باسنمو','buttocks'),('کصتو','genitals')]:
        intent=v2.parse_image_intent(v2.normalize_request_v2(f'عکس بده {word} ببینم'))
        assert region in intent.body_visibility.regions
    med=v2.parse_image_intent(v2.normalize_request_v2('درد کص دارم توضیح پزشکی بده'))
    assert not med.is_image_request


def test_prompt_single_subject_contract_and_round_trip():
    intent=v2.parse_image_intent(v2.normalize_request_v2('عکس بده ممه هاتو ببینم'))
    merged=v2.merge_image_intent(intent)
    profile=PartnerVisualProfile(user_id=1, version=2, fictional_age=24, base_seed=42, partner_name='Mina', gender_presentation='adult woman', face_description='oval face', hair_description='dark wavy hair', eye_description='brown eyes', skin_description='warm skin', body_description='average build', distinguishing_details='small dimple', profile_json={})
    plan=v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), profile, message_id=12, user_request='x')
    compiled=v2.compile_image_prompt(plan)
    assert 'exactly one fictional adult person' in compiled.positive_prompt
    assert 'two people' in compiled.negative_prompt
    restored=v2.deserialize_resolved_plan(v2.plan_to_json(plan))
    assert v2.plan_to_json(restored) == v2.plan_to_json(plan)
    assert isinstance(restored.scene, v2.ResolvedField)
