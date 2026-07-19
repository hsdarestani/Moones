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
    assert toks[1].suffixes == ['ها','ت']


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


def test_sofa_span_relation_and_no_fallback_regression():
    req=v2.normalize_request_v2('یه عکس روی مبل بده')
    intent=v2.parse_image_intent(req)
    sofa_token=next(i for i,t in enumerate(req.tokens) if t['stem']=='مبل')
    sofa_match=next(m for m in intent.parse_coverage.semantic_matches if m.canonical=='sofa')
    assert sofa_match.token_start_index == sofa_token
    assert intent.scene.spatial_relations[0].relation == 'on'
    assert intent.scene.spatial_relations[0].object == 'sofa'
    assert 'مبل' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert not intent.parse_coverage.fallback_required


def test_adult_morphology_visibility_no_residual_ha():
    intent=v2.parse_image_intent(v2.normalize_request_v2('عکس بده ممه هاتو ببینم'))
    assert 'ها' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert any(m.category == 'visibility_request' for m in intent.parse_coverage.semantic_matches)
    assert intent.body_visibility.regions['breasts'].visibility_requested
    assert intent.content_classification != v2.ContentClassification.NORMAL
    assert not intent.parse_coverage.fallback_required


def test_genital_denial_before_billing_semantics():
    intent=v2.parse_image_intent(v2.normalize_request_v2('عکس بده کصتو ببینم'))
    assert 'ها' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert intent.body_visibility.regions['genitals'].visibility_requested
    assert v2.evaluate_safety_policy(intent, v2.AdultImagePolicyContext()).reason_code == 'explicit_genital_visibility_not_supported'


def test_full_nudity_requires_policy_context_no_clothing_downgrade():
    intent=v2.parse_image_intent(v2.normalize_request_v2('عکس بده لخت باشی توش'))
    assert intent.content_classification == v2.ContentClassification.FULL_NUDITY
    assert v2.evaluate_safety_policy(intent).reason_code == 'adult_policy_context_required'
    ctx=v2.AdultImagePolicyContext(adult_enabled=True, adult_addon_owned=True, adult_addon_enabled=True, fictional_partner_min_age=24)
    assert v2.evaluate_safety_policy(intent, ctx).decision == v2.PolicyDecision.ALLOW


def test_no_global_phrase_presence_marks_unrelated_token():
    req=v2.normalize_request_v2('غریبه بی ربط اینجا مبل بده')
    matches=v2._semantic_matches(v2.IMAGE_SEMANTIC_LEXICONS['support_surfaces'], req.tokens, req.normalized_text)
    sofa=next(m for m in matches if m.canonical=='sofa')
    assert sofa.token_start_index == 4
    assert sofa.start == req.tokens[4]['start']
    assert not any(m.token_start_index == 0 and m.canonical == 'sofa' for m in matches)


def test_v2_best_effort_reaches_billing_instead_of_parser_denial(monkeypatch):
    from app.services import image_generation_service as svc
    class FakeDb:
        bind = None
        def scalar(self, *a, **k): return None
        def get(self, *a, **k): return None
        def flush(self): raise AssertionError('flush should not run before parser fallback')
    called={'reserve':False}
    monkeypatch.setattr(svc, 'user_has_addon', lambda *a, **k: True)
    monkeypatch.setattr(svc, 'user_addon_enabled', lambda *a, **k: True)
    monkeypatch.setattr(svc, '_build_request_context', lambda *a, **k: (None, {}, None, [], [], None, {}))
    monkeypatch.setattr(svc, 'ensure_visual_profile', lambda *a, **k: v2.ReadOnlyProfileAdapter())
    monkeypatch.setattr(v2, 'ensure_visual_profile_v2', lambda *a, **k: v2.ReadOnlyProfileAdapter())
    def reserve(*a, **k):
        called['reserve']=True
        raise RuntimeError('stop after billing boundary')
    monkeypatch.setattr(svc.UsageBillingService, 'reserve', reserve)
    user=User(id=1, telegram_id=123)
    try:
        svc._enqueue_image_request_v2(FakeDb(), user=user, chat_id=1, source_telegram_message_id=2, user_request='عکس بده غریبه روی مبل')
    except RuntimeError as exc:
        assert str(exc) == 'stop after billing boundary'
    assert called['reserve']


def _v2_profile():
    return PartnerVisualProfile(user_id=1, version=2, fictional_age=24, base_seed=42, partner_name='Mina', gender_presentation='adult woman', profile_json={'face_shape':'oval','eye_color':'brown','hair_color':'dark','skin_tone':'warm','build':'average'})


def _plan_for_text(text):
    intent=v2.parse_image_intent(v2.normalize_request_v2(text))
    merged=v2.merge_image_intent(intent)
    plan=v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), _v2_profile(), message_id=33, user_request=text)
    return intent, plan, v2.compile_image_prompt(plan)


def test_pose_only_reclining_infers_living_room_sofa_and_prompt():
    intent, plan, compiled = _plan_for_text('عکس بده لم داده')
    assert str(intent.continuity.action) == 'new_generation'
    assert plan.pose.value == 'reclining'
    assert plan.scene.value is None
    assert plan.support_surface.value is None
    assert plan.support_surface.source == v2.Provenance.SYSTEM
    assert not plan.support_surface.explicit_current_request
    assert plan.required_objects.value == []
    assert str(v2.InvariantCode.POSE_SUPPORT_MISMATCH) not in v2.validate_plan_invariants(plan)
    assert 'reclining' in compiled.positive_prompt
    assert 'sofa' not in compiled.positive_prompt
    assert 'chair' not in compiled.positive_prompt


def test_pose_only_lying_infers_sofa_without_invariant_failure():
    intent, plan, compiled = _plan_for_text('عکس بده دراز کشیده')
    assert plan.pose.value == 'lying'
    assert plan.scene.value is None
    assert plan.support_surface.value is None
    assert not v2.validate_plan_invariants(plan)
    assert 'lying' in compiled.positive_prompt and 'sofa' not in compiled.positive_prompt


def test_explicit_sofa_for_reclining_is_preserved():
    intent, plan, compiled = _plan_for_text('عکس بده لم داده روی مبل')
    assert plan.pose.value == 'reclining'
    assert plan.support_surface.value == 'sofa'
    assert plan.support_surface.source == v2.Provenance.EXPLICIT
    assert plan.support_surface.explicit_current_request
    assert not v2.validate_plan_invariants(plan)


def test_explicit_bed_for_lying_moves_scene_consistently_to_bed():
    intent, plan, compiled = _plan_for_text('عکس بده دراز کشیده روی تخت')
    assert plan.pose.value == 'lying'
    assert plan.support_surface.value == 'bed'
    assert plan.support_surface.source == v2.Provenance.EXPLICIT
    assert plan.scene.value == 'bed'
    assert 'bed' in plan.required_objects.value
    assert 'bed' in compiled.positive_prompt
    assert not v2.validate_plan_invariants(plan)


def test_explicit_lying_chair_conflict_blocks_before_billing_and_enqueue(monkeypatch):
    from app.services import image_generation_service as svc
    class FakeDb:
        bind = None
        def scalar(self, *a, **k): return None
        def get(self, *a, **k): return None
        def add(self, *a, **k): raise AssertionError('job enqueue should not happen')
        def flush(self): raise AssertionError('flush should not happen')
    called={'reserve':False}
    monkeypatch.setattr(svc, 'user_has_addon', lambda *a, **k: True)
    monkeypatch.setattr(svc, 'user_owns_addon', lambda *a, **k: True)
    monkeypatch.setattr(svc, 'user_addon_enabled', lambda *a, **k: True)
    monkeypatch.setattr(svc, '_build_request_context', lambda *a, **k: (None, {}, None, [], [], None, {}))
    monkeypatch.setattr(svc, 'ensure_visual_profile', lambda *a, **k: _v2_profile())
    monkeypatch.setattr(v2, 'ensure_visual_profile_v2', lambda *a, **k: _v2_profile())
    def reserve(*a, **k):
        called['reserve']=True
        raise AssertionError('reserve should not run')
    monkeypatch.setattr(svc.UsageBillingService, 'reserve', reserve)
    user=User(id=1, telegram_id=123)
    try:
        svc._enqueue_image_request_v2(FakeDb(), user=user, chat_id=1, source_telegram_message_id=2, user_request='عکس بده دراز کشیده روی صندلی')
    except svc.ImageGenerationDenied as exc:
        assert 'explicit_pose_support_conflict' in str(exc)
    assert not called['reserve']


def test_canary_exit_readiness_keys_include_invariant_failure():
    from app.tools import image_v2_canary as canary
    report=canary.run_canary([{'request':'عکس بده لم داده','expected':{'fallback_required':False}}])
    assert report['invariant_failures'] == 0
    report['invariant_failures'] = 1
    must_zero=['parser_fallback_count','content_mode_mismatches','route_mismatches','scene_mismatches','support_surface_mismatches','policy_mismatches','adult_to_normal_downgrades','invariant_failures','prompt_validation_failures','single_subject_constraint_failures','identity_fingerprint_changes','plan_round_trip_failures','source_plan_inheritance_failures','billing_before_validation_failures','failure_count']
    assert not all(report.get(k,0)==0 for k in must_zero)


def test_real_shadow_suffix_regressions_keep_lexical_words():
    toks=normalize_and_tokenize('تخت باش پارک مریم').tokens
    by={t.normalized:t for t in toks}
    assert by['تخت'].stem == 'تخت' and by['تخت'].suffixes == []
    assert by['باش'].stem == 'باش' and by['باش'].suffixes == []
    assert by['پارک'].stem == 'پارک' and by['پارک'].suffixes == []
    assert by['مریم'].stem == 'مریم' and by['مریم'].suffixes == []
    assert normalize_and_tokenize('بازوهات').tokens[0].suffixes == ['ها','ت']
    assert normalize_and_tokenize('لبات').tokens[0].stem == 'لب'


def test_real_shadow_park_refinement_no_false_visibility_or_ba():
    req=v2.normalize_request_v2('این بار تو پارک باش')
    intent=v2.parse_image_intent(req)
    assert intent.continuity.action == v2.ImageAction.REFINEMENT
    assert intent.scene.scene_key == 'park'
    assert next(t for t in req.tokens if t['normalized']=='باش')['stem'] == 'باش'
    assert not any(m.category == 'visibility_request' for m in intent.parse_coverage.semantic_matches)
    assert 'با' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert not intent.parse_coverage.fallback_required


def test_real_shadow_arms_image_of_framing_consumes_az():
    intent=v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده از بازوهات'))
    assert 'arms' in intent.body_visibility.regions
    assert intent.body_visibility.regions['arms'].framing_requested
    assert 'از' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert 'بازو' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert not intent.parse_coverage.fallback_required


def test_real_shadow_lips_pursed_not_bare_visibility_and_prompt():
    intent, plan, compiled = _plan_for_text('یه عکس بده لبات قنچه باشه')
    assert 'lips' in intent.body_visibility.regions
    assert any(e.region == 'lips' and e.attribute == 'shape/expression' and e.value == 'pursed' for e in intent.expression_modifiers)
    assert not any(m.category == 'visibility_request' and m.normalized_variant == 'باشه' for m in intent.parse_coverage.semantic_matches)
    assert not intent.parse_coverage.fallback_required
    assert 'pursed' in compiled.positive_prompt


def test_real_shadow_lying_on_bed_keeps_bed_stem():
    req=v2.normalize_request_v2('عکس بده دراز کشیده روی تخت')
    assert next(t for t in req.tokens if t['normalized']=='تخت')['stem'] == 'تخت'
    intent, plan, _ = _plan_for_text('عکس بده دراز کشیده روی تخت')
    assert intent.scene.scene_key == 'bed'
    assert plan.scene.value == 'bed'
    assert plan.support_surface.value == 'bed'
    assert plan.pose.value == 'lying'


def test_content_bearing_unknown_visual_token_becomes_passthrough_best_effort():
    intent=v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده روی مبل با زلمبو'))
    assert 'زلمبو' in intent.parse_coverage.unmatched_meaningful_tokens
    assert intent.parse_coverage.disposition == v2.ParseDisposition.BEST_EFFORT
    assert 'زلمبو' in intent.passthrough_visual_details
    assert not intent.parse_coverage.fallback_required


def test_route_shadow_detects_legacy_chat_mismatch_for_explicit_images():
    for text in ['یه عکس معمولی توی کافه بده','یه عکس روی مبل بده','عکس بده لم داده','عکس بده ممه هاتو ببینم']:
        shadow=v2.route_shadow_decision(text, source_message_id=44, legacy_route='chat')
        assert shadow['legacy_route'] == 'chat'
        assert shadow['v2_is_image_request']
        assert shadow['v2_detected_action'] == 'new_generation'


def test_full_shadow_result_is_compact_read_only():
    result=v2.shadow_plan_read_only('یه عکس بده لبات قنچه باشه', user_id=1, chat_id=2, source_message_id=3, legacy_route='chat')
    assert result['fallback_required'] is False
    assert result['body_regions'] == ['lips']
    assert result['expression_modifiers'][0]['value'] == 'pursed'
    assert 'identity_fingerprint' in result
    assert 'prompt' not in result


def test_v2_flag_resolution_truth_table_and_failure(monkeypatch):
    from app.services import settings_service
    from app.services.image_pipeline_v2_flags import resolve_image_pipeline_v2_flags

    class FakeSettings:
        values = {}
        def get_bool(self, db, key, default=False):
            return self.values.get(key, default)

    monkeypatch.setattr(settings_service, 'SettingsService', FakeSettings)
    cases = [
        ({}, (False, False, False, False)),
        ({'image_generation.pipeline_v2_shadow_mode': True}, (False, True, False, False)),
        ({'image_generation.pipeline_v2_enabled': True, 'image_generation.pipeline_v2_shadow_mode': True}, (False, True, True, False)),
        ({'image_generation.pipeline_v2_enabled': True, 'image_generation.pipeline_v2_production_approved': True, 'image_generation.pipeline_v2_shadow_mode': True}, (True, False, True, True)),
    ]
    for values, expected in cases:
        FakeSettings.values = values
        flags = resolve_image_pipeline_v2_flags(object())
        assert (flags.execution_enabled, flags.shadow_enabled, flags.raw_enabled, flags.production_approved) == expected

    class FailingSettings:
        def get_bool(self, db, key, default=False):
            raise RuntimeError('settings down')

    monkeypatch.setattr(settings_service, 'SettingsService', FailingSettings)
    flags = resolve_image_pipeline_v2_flags(object())
    assert flags.execution_enabled is False
    assert flags.shadow_enabled is False


def test_route_shadow_gate_disabled_does_not_import_or_log(monkeypatch, caplog):
    from app.api import telegram
    from app.services.image_pipeline_v2_flags import ImagePipelineV2Flags

    monkeypatch.setattr(telegram, 'resolve_image_pipeline_v2_flags', lambda db: ImagePipelineV2Flags(False, False, False, False))
    called = {'route': False}
    monkeypatch.setattr(v2, 'route_shadow_decision', lambda *a, **k: called.__setitem__('route', True))
    with caplog.at_level('INFO'):
        assert telegram._log_image_v2_route_shadow_if_enabled(object(), text='یه عکس خاموش بده', source_message_id=901, legacy_route='chat') is False
    assert called['route'] is False
    assert 'IMAGE_V2_ROUTE_SHADOW' not in caplog.text


def test_route_shadow_gate_enabled_chat_and_image_logs_compact(monkeypatch, caplog):
    from app.api import telegram
    from app.services.image_pipeline_v2_flags import ImagePipelineV2Flags

    monkeypatch.setattr(telegram, 'resolve_image_pipeline_v2_flags', lambda db: ImagePipelineV2Flags(False, True, False, False))
    calls = []
    def fake_route(text, *, source_message_id=None, legacy_route='chat'):
        calls.append((text, source_message_id, legacy_route))
        return {'request_hash': 'abc123', 'source_message_id': source_message_id, 'legacy_route': legacy_route, 'v2_is_image_request': True, 'compiled_positive_prompt': 'SHOULD_NOT_LOG'}
    monkeypatch.setattr(v2, 'route_shadow_decision', fake_route)
    raw = 'RAW USER TEXT عکس خصوصی'
    with caplog.at_level('INFO'):
        assert telegram._log_image_v2_route_shadow_if_enabled(object(), text=raw, source_message_id=902, legacy_route='chat') is True
        assert telegram._log_image_v2_route_shadow_if_enabled(object(), text=raw, source_message_id=903, legacy_route='image_explicit') is True
    assert calls == [(raw, 902, 'chat'), (raw, 903, 'image_explicit')]
    assert 'request_hash' in caplog.text and 'source_message_id' in caplog.text
    assert raw not in caplog.text
    assert 'positive_prompt' not in caplog.text
    assert 'negative_prompt' not in caplog.text


def test_enqueue_shadow_uses_central_flags_and_fails_closed(monkeypatch, caplog):
    from app.services import image_generation_service as svc
    from app.services import image_pipeline_v2_flags as flags_mod
    from app.services.image_pipeline_v2_flags import ImagePipelineV2Flags

    class FakeDb:
        bind = None
        def scalar(self, *a, **k): return None

    user = User(id=1, telegram_id=77)
    called = {'shadow': 0, 'v2': 0, 'reserve': 0}
    monkeypatch.setattr(svc, 'user_has_addon', lambda *a, **k: False)
    monkeypatch.setattr(svc, 'user_addon_enabled', lambda *a, **k: False)
    monkeypatch.setattr(v2, 'shadow_plan_read_only', lambda *a, **k: called.__setitem__('shadow', called['shadow'] + 1) or {'request_hash': 'h', 'source_message_id': 10})
    monkeypatch.setattr(svc, '_enqueue_image_request_v2', lambda *a, **k: called.__setitem__('v2', called['v2'] + 1))
    monkeypatch.setattr(svc.UsageBillingService, 'reserve', lambda *a, **k: called.__setitem__('reserve', called['reserve'] + 1))

    monkeypatch.setattr(flags_mod, 'resolve_image_pipeline_v2_flags', lambda db: ImagePipelineV2Flags(False, False, False, False))
    try:
        svc.enqueue_image_request(FakeDb(), user=user, chat_id=1, source_telegram_message_id=10, user_request='عکس خاموش')
    except svc.ImageGenerationDenied as exc:
        assert str(exc) == 'addon_required'
    assert called == {'shadow': 0, 'v2': 0, 'reserve': 0}

    monkeypatch.setattr(flags_mod, 'resolve_image_pipeline_v2_flags', lambda db: ImagePipelineV2Flags(False, True, True, False))
    with caplog.at_level('INFO'):
        try:
            svc.enqueue_image_request(FakeDb(), user=user, chat_id=1, source_telegram_message_id=10, user_request='عکس سایه')
        except svc.ImageGenerationDenied:
            pass
    assert called['shadow'] == 1
    assert called['v2'] == 0
    assert called['reserve'] == 0
    assert 'IMAGE_V2_SHADOW_RESULT' in caplog.text

    monkeypatch.setattr(flags_mod, 'resolve_image_pipeline_v2_flags', lambda db: ImagePipelineV2Flags(True, False, True, True))
    svc.enqueue_image_request(FakeDb(), user=user, chat_id=1, source_telegram_message_id=10, user_request='عکس اجرا')
    assert called['v2'] == 1
    assert called['shadow'] == 1


def test_simple_persian_visibility_request_phrases_do_not_fallback():
    cases = [
        'یه عکس بده',
        'یه عکس بده ببینمت',
        'یه عکس بده ببینمت خبب',
        'بذار ببینمت',
        'خودتو نشونم بده',
        'می‌خوام ببینمت',
        'نشونم بده',
    ]
    for text in cases:
        intent = v2.parse_image_intent(v2.normalize_request_v2(text))
        assert intent.is_image_request, text
        assert intent.continuity.action == v2.ImageAction.NEW_GENERATION, text
        assert not intent.parse_coverage.fallback_required, (text, intent.parse_coverage.unmatched_meaningful_tokens)


def test_harmless_filler_normalization_is_bounded():
    assert normalize_and_tokenize('خب خبب خببب').normalized == 'خب خب خب'
    assert normalize_and_tokenize('زلمبوو').tokens[0].normalized == 'زلمبوو'


def test_simple_visibility_requests_enqueue_without_parser_uncertain(monkeypatch):
    from app.services import image_generation_service as svc
    from app.services.image_prompt_engine import ImageRouteDecision

    class FakeCharge:
        id = 9

    class FakeDb:
        bind = None
        def __init__(self): self.jobs = []
        def scalar(self, *a, **k): return None
        def get(self, *a, **k): return None
        def add(self, obj): self.jobs.append(obj)
        def flush(self):
            for i, job in enumerate(self.jobs, 1):
                if getattr(job, 'id', None) is None:
                    job.id = i

    monkeypatch.setattr(svc, 'user_has_addon', lambda *a, **k: True)
    monkeypatch.setattr(svc, 'user_owns_addon', lambda *a, **k: False)
    monkeypatch.setattr(svc, 'user_addon_enabled', lambda *a, **k: True)
    monkeypatch.setattr(svc, '_build_request_context', lambda *a, **k: (None, {}, None, [], [], None, {}))
    monkeypatch.setattr(svc, 'inspect', lambda bind: type('I', (), {'get_table_names': lambda self: []})())
    monkeypatch.setattr(svc, 'image_generation_quote', lambda *a, **k: object())
    monkeypatch.setattr(svc.UsageBillingService, 'reserve', lambda *a, **k: FakeCharge())
    monkeypatch.setattr(svc, 'ensure_visual_profile', lambda *a, **k: _v2_profile())
    monkeypatch.setattr(v2, 'ensure_visual_profile_v2', lambda *a, **k: _v2_profile())

    user = User(id=1, telegram_id=123)
    route = ImageRouteDecision(route='image_explicit', explicit_image_request=True, confidence=.95, reason_code='explicit_image_request')
    for i, text in enumerate(['یه عکس بده ببینمت', 'یه عکس بده ببینمت خبب', 'بذار ببینمت', 'خودتو نشونم بده'], 1):
        db = FakeDb()
        job = svc._enqueue_image_request_v2(db, user=user, chat_id=1, source_telegram_message_id=i, user_request=text, route_decision=route)
        assert job in db.jobs
        assert job.image_action == v2.ImageAction.NEW_GENERATION


def test_unknown_visual_constraint_still_compiles_best_effort():
    intent = v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده روی مبل با زلمبو'))
    assert 'زلمبو' in intent.parse_coverage.unmatched_meaningful_tokens
    assert intent.parse_coverage.disposition == v2.ParseDisposition.BEST_EFFORT
    assert not intent.parse_coverage.fallback_required


def test_normal_persian_image_request_compiles_without_policy_jargon():
    req=v2.normalize_request_v2('یه عکس بده ببینمت')
    intent=v2.parse_image_intent(req)
    assert intent.content_classification == v2.ContentClassification.NORMAL
    merged=v2.merge_image_intent(intent)
    profile=PartnerVisualProfile(user_id=1, version=2, fictional_age=30, base_seed=42, profile_json={})
    plan=v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), profile, message_id=70, user_request=req.raw_text)
    compiled=v2.compile_image_prompt(plan)
    forbidden=['Body visibility','explicit body','policy-resolved','adult visual','visibility','policy','nudity']
    assert not any(term in compiled.positive_prompt for term in forbidden)
    assert 'Exactly one person, no duplicate subject, no collage' in compiled.positive_prompt


def test_adult_level_classifications_and_genital_closeup_exclusion():
    cases = {
        "یه عکس لینجری بفرست": v2.ContentClassification.LINGERIE,
        "یه عکس شیطون‌تر بفرست": v2.ContentClassification.SUGGESTIVE,
        "بالاتنه برهنه باش": v2.ContentClassification.TOPLESS,
        "نمای نزدیک اندام تناسلی": v2.ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY,
    }
    for text, classification in cases.items():
        intent = v2.parse_image_intent(v2.normalize_request_v2(text))
        assert intent.content_classification == classification
        assert not intent.parse_coverage.fallback_required
    exclusion = v2.parse_image_intent(v2.normalize_request_v2("بدون نمای نزدیک اندام تناسلی"))
    assert exclusion.content_classification != v2.ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
    assert "genital_closeup" in exclusion.explicit_exclusions


def test_production_full_nudity_request_routes_parses_and_compiles_without_fallback():
    text = "یه عکس کاملاً برهنه و بدون هیچ لباسی، تمام‌قد، بدون نمای نزدیک اندام تناسلی بفرست"
    intent = v2.parse_image_intent(v2.normalize_request_v2(text))
    assert intent.is_image_request
    assert intent.continuity.action == v2.ImageAction.NEW_GENERATION
    assert intent.content_classification == v2.ContentClassification.FULL_NUDITY
    assert intent.composition.framing == "full_body"
    assert "genital_closeup" in intent.explicit_exclusions
    assert not intent.parse_coverage.fallback_required
    for token in ["کاملا", "برهنه", "لباس", "تمامقد", "نمای", "نزدیک", "اندام", "تناسلی"]:
        assert token not in intent.parse_coverage.unmatched_meaningful_tokens
    ctx = v2.AdultImagePolicyContext(adult_enabled=True, adult_addon_owned=True, adult_addon_enabled=True, fictional_partner_min_age=24)
    safety = v2.evaluate_safety_policy(intent, ctx)
    assert safety.decision == v2.PolicyDecision.ALLOW
    plan = v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), safety, _v2_profile(), message_id=77, user_request=text)
    compiled = v2.compile_image_prompt(plan)
    assert "full nudity" in compiled.positive_prompt
    assert "no genital close-up" in compiled.positive_prompt or "genital_closeup" in compiled.negative_prompt
    assert "context-appropriate clothing" not in compiled.positive_prompt


def test_explicit_neighbor_kissing_parses_two_adult_subjects():
    intent = v2.parse_image_intent(v2.normalize_request_v2('یه عکس در حال بوسیدن همسایه بده'))
    assert intent.is_image_request is True
    assert intent.continuity.action == v2.ImageAction.NEW_GENERATION
    assert intent.interaction == 'kiss'
    assert intent.secondary_subject.role == 'neighbor'
    assert intent.content_classification == v2.ContentClassification.SUGGESTIVE
    assert intent.parse_coverage.fallback_required is False
    assert 'بوسیدن' not in intent.parse_coverage.unmatched_meaningful_tokens
    assert 'همسایه' not in intent.parse_coverage.unmatched_meaningful_tokens
    plan = v2.construct_resolved_plan(
        intent,
        v2.merge_image_intent(intent),
        v2.SafetyDecision(),
        v2.ReadOnlyProfileAdapter(fictional_age=25),
        message_id=1,
        user_request='یه عکس در حال بوسیدن همسایه بده',
    )
    assert plan.composition['expected_subject_count'] == 2
    assert plan.composition['primary_subject_role'] == 'moones_partner'
    assert plan.composition['secondary_subject_role'] == 'neighbor'
    assert plan.composition['interaction'] == 'kiss'
    compiled = v2.compile_image_prompt(plan)
    assert 'exactly two fictional consenting adults' in compiled.positive_prompt
    assert 'one generic fictional adult neighbor' in compiled.positive_prompt
    assert 'third person' in compiled.negative_prompt


def test_ordinary_image_request_keeps_single_subject_count():
    intent = v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده'))
    plan = v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), v2.ReadOnlyProfileAdapter(), message_id=2, user_request='یه عکس بده')
    assert plan.composition['expected_subject_count'] == 1

NATURAL_PERSIAN_IMAGE_REQUESTS = [
    'یه عکس کنار پنجره زیر نور غروب بده','یه عکس با یه پیراهن قرمز خوشگل بفرست','یه عکس وقتی داری کیک درست می‌کنی بده','یه عکس خسته و نامرتب بعد از بیدار شدن بفرست','یه عکس توی بالکن در حالی که به خیابون نگاه می‌کنی بده','یه عکس با حال و هوای سینمایی و رنگ‌های گرم بساز','یه عکس در حال بوسیدن همسایه بده','یه عکس موهات خیسه و حوله دورته بفرست','یه عکس با ژست مغرور و نگاه مستقیم بده','یه عکس وسط آشپزخونه با لیوان آب دستت بفرست','یه عکس با هودی گشاد مشکی توی بالکن بده','یه عکس وقتی داری ماکارونی درست می‌کنی بفرست','یه عکس زیر نور آبی اتاق با نگاه خسته بده','یه عکس موهات خیس و نامرتبه بفرست','یه عکس با لباس مخمل زرشکی کنار پنجره بده','یه عکس توی راهرو هتل در حال راه رفتن بده','یه عکس در حال خندیدن با یه فنجون چای بفرست','یه عکس کنار همسایه در حال بوسیدن بده','یه عکس کاملاً برهنه تمام‌قد بدون نمای نزدیک بده','یه عکس با لباس زیر روی تخت ولی سلفی نباشه','یه عکس مثل قبلی ولی نورش گرم‌تر باشه','یه عکس با ژست خاص و حس سینمایی بده','یه عکس با لباس عجیب فضایی بفرست','عکس با شال آبی و بارون پشت شیشه بده','یه سلفی تو آینه با نور کم بفرست','عکس تمام قد با کفش سفید بده','یه عکس تو کافه کنار میز چوبی بده','یه عکس تو پارک با برگای پاییزی بده','یه عکس لب ساحل با باد توی موهات بده','یه عکس تو ماشین با عینک آفتابی بده','یه عکس روی مبل با کتاب دستت بده','یه عکس با لبخند آروم و نگاه پایین بده','یه عکس تو اتاق خواب با چراغ خواب روشن بده','یه عکس بیرون زیر برف بفرست','یه عکس با کت چرم مشکی بده','یه عکس با مانتوی بلند کرم بفرست','یه عکس توی آسانسور هتل بده','یه عکس کنار گلدون بزرگ بده','یه عکس با آرایش ملایم و رژ قرمز بده','یه عکس در حال رقصیدن تو اتاق بده','یه عکس در حال قدم زدن زیر بارون بده','یه عکس با حوله دور بدن تو حمام بده','یه عکس خواب‌آلود روی تخت بده','یه عکس نیمه‌لخت روی تخت بده','یه عکس لخت تمام قد بفرست','یه عکس با لباس زیر مشکی بده','یه عکس کنار یه مرد خیالی بزرگسال در حال بغل کردن بده','یه عکس کنار دختر همسایه خیالی بزرگسال بده','یه عکس دست تو دست یه آدم خیالی بزرگسال بده','یه عکس بدون عینک و بدون کلاه بده','یه عکس موهات باز باشه ولی عینک نباشه','یه عکس از پشت پنجره ولی نمای نزدیک نباشه','عکص با هودی مشکلی بده','ی عکس تو بالکون بفرس','یه عکس شاد و شلوغ تو آشپزخونه بده','یه عکس تاریک با نور نئون بنفش بده','یه عکس با لباس ساتن سبز کنار شومینه بده','یه عکس روی صندلی چوبی با لیوان آب بده','یه عکس با حس فیلم نوآر بده','یه عکس در حال نوشتن تو دفترچه بده',
]

CLARIFICATION_PERSIAN_IMAGE_REQUESTS = [
    'عکس جدید یا قبلی، خودت انتخاب کن',
    'همون رو عوض کن',
    'یه نفر باشه و سه نفر هم کنارش باشن',
    'بچه باشه یا بزرگسال فرقی نداره',
]

DENIED_PERSIAN_IMAGE_REQUESTS = [
    'یه عکس بده واژن معلوم باشه',
    'یه عکس بده از کصت نمای نزدیک',
]


def test_broad_natural_persian_dataset_best_effort_not_parser_uncertain():
    proceed=0
    for text in NATURAL_PERSIAN_IMAGE_REQUESTS:
        intent=v2.parse_image_intent(v2.normalize_request_v2(text))
        assert intent.is_image_request, text
        assert intent.continuity.action != v2.ImageAction.CHAT
        assert intent.parse_coverage.disposition in {v2.ParseDisposition.COMPLETE, v2.ParseDisposition.BEST_EFFORT}, text
        assert not intent.parse_coverage.fallback_required
        assert intent.parse_coverage.clarification_reason != 'image_parser_uncertain'
        proceed += 1
    assert len(NATURAL_PERSIAN_IMAGE_REQUESTS) >= 60
    assert proceed >= 45
    assert proceed / (len(NATURAL_PERSIAN_IMAGE_REQUESTS) + len(CLARIFICATION_PERSIAN_IMAGE_REQUESTS) + len(DENIED_PERSIAN_IMAGE_REQUESTS)) >= .68


def test_passthrough_visual_details_are_preserved_in_prompt():
    text='یه عکس با پیراهن زرشکی مخملی کنار شومینه بده'
    intent, plan, compiled = _plan_for_text(text)
    assert intent.parse_coverage.disposition == v2.ParseDisposition.BEST_EFFORT
    assert any('زرشکی' in x or 'مخملی' in x or 'شومینه' in x for x in intent.passthrough_visual_details)
    assert 'User-requested visual details:' in compiled.positive_prompt
    assert 'زرشکی' in compiled.positive_prompt or 'مخملی' in compiled.positive_prompt or 'شومینه' in compiled.positive_prompt


def test_actual_critical_ambiguities_still_clarify_and_denials_remain_policy():
    for text in CLARIFICATION_PERSIAN_IMAGE_REQUESTS:
        intent=v2.parse_image_intent(v2.normalize_request_v2(text))
        assert intent.parse_coverage.disposition == v2.ParseDisposition.CLARIFICATION_REQUIRED, text
        assert intent.parse_coverage.clarification_reason in {'image_action_ambiguous','image_source_ambiguous','image_composition_conflict','image_safety_detail_ambiguous'}
    for text in DENIED_PERSIAN_IMAGE_REQUESTS:
        intent=v2.parse_image_intent(v2.normalize_request_v2(text))
        assert v2.evaluate_safety_policy(intent).decision == v2.PolicyDecision.DENY


def _profile(name='امیر', age=21, gender='masculine'):
    return PartnerVisualProfile(user_id=2, version=2, fictional_age=age, base_seed=123, partner_name=name, gender_presentation=gender, profile_json={})


def test_amir_minimal_identity_prompt_has_no_hardcoded_creative_defaults():
    req=v2.normalize_request_v2('یه عکس بده')
    intent=v2.parse_image_intent(req)
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), _profile(), message_id=1, user_request=req.raw_text)
    compiled=v2.compile_image_prompt(plan)
    assert 'fictional_age=21' in compiled.positive_prompt
    assert 'gender_presentation=masculine' in compiled.positive_prompt
    forbidden=['30-year-old woman','comfortable living room','chair','seated','natural soft light','warm familiar expression']
    assert not any(x in compiled.positive_prompt for x in forbidden)
    assert 'امیر' not in compiled.sections['passthrough_visual_details']
    assert plan.scene.source == v2.Provenance.SYSTEM and plan.scene.value is None
    assert plan.composition['field_provenance']['scene'] == str(v2.Provenance.SYSTEM)


def test_recent_conversation_context_resolves_cafe_activity_with_provenance():
    class Msg:
        content='امیر الان تو کافه است و قهوه می‌نوشد'
    intent=v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده'))
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent, recent_context=[Msg()]), v2.SafetyDecision(), _profile(), message_id=2, user_request='یه عکس بده')
    assert plan.scene.value == 'cafe'
    assert plan.activity.value == 'drinking coffee'
    assert plan.scene.source == v2.Provenance.RECENT
    assert plan.activity.source == v2.Provenance.RECENT


def test_current_request_overrides_recent_and_routine_context():
    class Msg: content='امیر تو کافه قهوه می‌خورد'
    intent=v2.parse_image_intent(v2.normalize_request_v2('یه عکس تو پارک در حال قدم زدن بده'))
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent, recent_context=[Msg()], routine_context={'location':'cafe','slot_name':'evening'}), v2.SafetyDecision(), _profile(), message_id=3, user_request='یه عکس تو پارک در حال قدم زدن بده')
    assert plan.scene.value == 'park'
    assert plan.activity.value == 'walking'
    assert plan.scene.source == v2.Provenance.EXPLICIT


def test_black_coat_correction_and_selfie_provenance():
    intent=v2.parse_image_intent(v2.normalize_request_v2('این بار کت مشکی بپوش و سلفی بده'))
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), _profile(), message_id=4, user_request='این بار کت مشکی بپوش و سلفی بده')
    assert plan.wardrobe.value == 'black coat'
    assert plan.wardrobe.source in {v2.Provenance.EXPLICIT, v2.Provenance.CORRECTION}
    assert plan.camera.value == 'selfie'
    assert 'selfie' in v2.compile_image_prompt(plan).positive_prompt


def test_gender_presentations_do_not_add_stereotyped_traits():
    for gender in ['feminine','masculine','neutral']:
        plan=v2.construct_resolved_plan(v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده')), v2.merge_image_intent(v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده'))), v2.SafetyDecision(), _profile(gender=gender), message_id=5, user_request='یه عکس بده')
        prompt=v2.compile_image_prompt(plan).positive_prompt
        assert 'beard' not in prompt and 'short hair' not in prompt and 'menswear' not in prompt
        assert 'makeup' not in prompt and 'dress' not in prompt and 'long hair' not in prompt
        assert 'She is' not in prompt and 'He is' not in prompt and 'Show her' not in prompt


def test_validate_rejects_rendered_unspecified_field():
    plan=v2.construct_resolved_plan(v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده')), v2.merge_image_intent(v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده'))), v2.SafetyDecision(), _profile(), message_id=6, user_request='یه عکس بده')
    compiled=v2.compile_image_prompt(plan)
    compiled.sections['scene']='living_room'
    assert str(v2.InvariantCode.UNSPECIFIED_RENDERED) in v2.validate_compiled_prompt(plan, compiled)


def _anatomy_plan(text, *, gender='masculine', anatomical_profile=None):
    req=v2.normalize_request_v2(text)
    intent=v2.parse_image_intent(req)
    p=_profile(gender=gender)
    p.anatomical_profile=anatomical_profile
    return v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), p, message_id=10, user_request=req.raw_text)


def test_full_nudity_prompt_preserves_masculine_presentation_and_male_anatomy():
    plan=_anatomy_plan('کاملاً لخت عکس بده', gender='masculine', anatomical_profile='male')
    compiled=v2.compile_image_prompt(plan)
    assert plan.visual_requirements.anatomical_profile == 'male'
    assert plan.visual_requirements.anatomy_source == 'explicit_profile'
    assert plan.visual_requirements.anatomy_consistency_required is True
    assert plan.visual_requirements.anatomy_qa_required is True
    assert 'gender_presentation=masculine' in compiled.positive_prompt
    assert 'Anatomy must be consistently male' in compiled.positive_prompt
    assert 'contradictory anatomy' in compiled.negative_prompt


def test_full_nudity_prompt_preserves_feminine_presentation_and_female_anatomy():
    plan=_anatomy_plan('کاملاً لخت عکس بده', gender='feminine', anatomical_profile='female')
    compiled=v2.compile_image_prompt(plan)
    assert plan.visual_requirements.anatomical_profile == 'female'
    assert 'gender_presentation=feminine' in compiled.positive_prompt
    assert 'Anatomy must be consistently female' in compiled.positive_prompt


def test_gender_presentation_does_not_infer_anatomical_profile_and_normal_metadata_omits_it():
    full=_anatomy_plan('کاملاً لخت عکس بده', gender='masculine', anatomical_profile=None)
    assert full.visual_requirements.anatomical_profile == 'unspecified'
    assert full.visual_requirements.anatomy_consistency_required is False
    normal=_anatomy_plan('یه عکس معمولی بده', gender='masculine', anatomical_profile='male')
    compiled=v2.compile_image_prompt(normal)
    assert normal.visual_requirements.anatomical_profile is None
    assert 'Anatomy must be consistently' not in compiled.positive_prompt
    assert 'contradictory anatomy' not in compiled.negative_prompt


def test_generate_new_request_instance_key_changes_composition_seed_only():
    fp = 'same-semantic-fingerprint'
    a = v2.resolve_image_seed(4242, v2.ImageAction.NEW_GENERATION, fp, request_instance_key=1001)
    b = v2.resolve_image_seed(4242, v2.ImageAction.NEW_GENERATION, fp, request_instance_key=1002)
    again = v2.resolve_image_seed(4242, v2.ImageAction.NEW_GENERATION, fp, request_instance_key=1001)
    assert a['final_provider_seed'] == again['final_provider_seed']
    assert a['final_provider_seed'] != b['final_provider_seed']
    assert a['identity_seed'] == b['identity_seed'] == 4242
    assert a['seed_family'] == b['seed_family']
    assert a['request_instance_key'] == '1001'
    assert a['seed_branch'] != b['seed_branch']


def test_retry_branch_is_deterministic_and_preserves_identity_family():
    fp = 'same-semantic-fingerprint'
    first = v2.resolve_image_seed(4242, v2.ImageAction.NEW_GENERATION, fp, request_instance_key=1001, retry_branch=0)
    retry = v2.resolve_image_seed(4242, v2.ImageAction.NEW_GENERATION, fp, request_instance_key=1001, retry_branch=1)
    retry_again = v2.resolve_image_seed(4242, v2.ImageAction.NEW_GENERATION, fp, request_instance_key=1001, retry_branch=1)
    assert retry == retry_again
    assert retry['final_provider_seed'] != first['final_provider_seed']
    assert retry['identity_seed'] == first['identity_seed']
    assert retry['seed_family'] == first['seed_family']
    assert retry['retry_branch'] == 1


def test_variation_and_refinement_ignore_new_request_instance_for_continuity_source():
    class Src:
        id = 9
        seed = 111111
        final_provider_seed = 111111
    fp = 'same-semantic-fingerprint'
    var_a = v2.resolve_image_seed(4242, v2.ImageAction.VARIATION, fp, Src(), ['pose'], request_instance_key=1001)
    var_b = v2.resolve_image_seed(4242, v2.ImageAction.VARIATION, fp, Src(), ['pose'], request_instance_key=1002)
    ref_a = v2.resolve_image_seed(4242, v2.ImageAction.REFINEMENT, fp, Src(), request_instance_key=1001)
    ref_b = v2.resolve_image_seed(4242, v2.ImageAction.REFINEMENT, fp, Src(), request_instance_key=1002)
    assert var_a['final_provider_seed'] == var_b['final_provider_seed']
    assert ref_a['final_provider_seed'] == ref_b['final_provider_seed']
    assert var_a['continuity_source_job_id'] == ref_a['continuity_source_job_id'] == 9
