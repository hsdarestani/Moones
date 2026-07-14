from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import StrEnum
from datetime import datetime, timedelta
import hashlib, json, re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.llm.image_client import DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, VENICE_SEED_MIN, VENICE_SEED_MAX
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact, PartnerVisualProfile
from app.models.user import User
from app.services.persian_normalization import normalize_and_tokenize

PROMPT_ENGINE_VERSION = 'image-prompt-v1.6.0'
PLAN_VERSION = 'resolved-image-plan-v2.0'
PROFILE_SCHEMA_VERSION = 2

class ImageAction(StrEnum):
    NEW_GENERATION='new_generation'; VARIATION='variation'; REFINEMENT='refinement'; RESEND_EXACT='resend_exact'; DENY='deny'; CHAT='chat'
class Provenance(StrEnum):
    EXPLICIT='explicit_current_request'; EXCLUSION='explicit_current_exclusions'; SOURCE_PLAN='source_image_plan'; RECENT='recent_same_chat_message'; MEMORY='recent_visual_memory'; ROUTINE='partner_routine'; PROFILE='profile_default'; SYSTEM='system_default'
class PolicyDecision(StrEnum):
    ALLOW='allow'; DENY='deny'; TRANSFORM='transform'
class InvariantCode(StrEnum):
    EXPLICIT_OVERWRITTEN='explicit_current_field_overwritten'; SUPPORT_SCENE_MISMATCH='support_surface_scene_mismatch'; POSE_SUPPORT_MISMATCH='pose_support_surface_mismatch'; REQUIRED_OBJECT_MISSING='required_object_missing'; INCOMPATIBLE_OBJECT_PRESENT='incompatible_object_present'; UNSUPPORTED_SAFETY_DOWNGRADE='unsupported_safety_intent_not_downgraded'; RESEND_HAS_GENERATION='resend_has_generation_plan'; VARIATION_SEED_UNCHANGED='variation_seed_unchanged'; SOURCE_SCOPE_INVALID='source_job_scope_invalid'; SOURCE_STALE='source_job_stale'; IDENTITY_INCOMPLETE='identity_profile_incomplete'; NULL_IDENTITY_DESCRIPTOR='identity_descriptor_null_like'; DIMENSION_ORIENTATION='dimension_orientation_mismatch'; PROMPT_CONTRADICTION='prompt_contradiction'

@dataclass
class ResolvedField:
    value: object = None; source: str = Provenance.SYSTEM; confidence: float = 1.0; explicit_current_request: bool = False; inherited: bool = False; source_message_id: int|None = None; source_image_job_id: int|None = None
@dataclass
class NormalizedImageRequest:
    raw_text: str; normalized_text: str; tokens: list[dict]; user_id: int|None=None; chat_id: int|None=None; source_message_id: int|None=None
@dataclass
class ImageRouteDecisionV2:
    action: str; reason_code: str; source_image_job_id: int|None=None; confidence: float=1.0
@dataclass
class BodyRegionIntent:
    mentioned: bool=False; visibility_requested: bool=False; visibility_negated: bool=False; framing_requested: bool=False; explicit_current_request: bool=False; source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class BodyVisibilityIntent:
    regions: dict[str, BodyRegionIntent]=field(default_factory=dict)
@dataclass
class SceneIntent: scene_key: str|None=None; support_surface: str|None=None; location: str|None=None; source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class PoseIntent: pose: str|None=None; source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class WardrobeIntent: wardrobe: str|None=None; exclusions: list[str]=field(default_factory=list)
@dataclass
class CompositionIntent: orientation: str|None=None; framing: str|None=None; camera: str|None=None
@dataclass
class ContinuityIntent: action: str=ImageAction.NEW_GENERATION; source_image_job_id: int|None=None
@dataclass
class IdentityIntent: consistency_level: str='best_effort_text_only'
@dataclass
class VisualAssertion: subject: str; attribute: str; polarity: str; source_span: tuple[int,int]; confidence: float=1.0
@dataclass
class ImageRequestIntent:
    is_image_request: bool=False; route: ImageRouteDecisionV2|None=None; body_visibility: BodyVisibilityIntent=field(default_factory=BodyVisibilityIntent); scene: SceneIntent=field(default_factory=SceneIntent); pose: PoseIntent=field(default_factory=PoseIntent); wardrobe: WardrobeIntent=field(default_factory=WardrobeIntent); composition: CompositionIntent=field(default_factory=CompositionIntent); continuity: ContinuityIntent=field(default_factory=ContinuityIntent); identity: IdentityIntent=field(default_factory=IdentityIntent); visual_assertions: list[VisualAssertion]=field(default_factory=list); explicit_exclusions: list[str]=field(default_factory=list)
@dataclass
class SafetyDecision: decision: str=PolicyDecision.ALLOW; reason_code: str|None=None; user_message_key: str|None=None; policy_version: str='image-safety-v2'
@dataclass
class ProviderImageCapabilities:
    supports_seed: bool=True; seed_min: int=VENICE_SEED_MIN; seed_max: int=VENICE_SEED_MAX; supports_reference_image: bool=False; supports_image_to_image: bool=False; supports_identity_conditioning: bool=False; supports_negative_prompt: bool=True; supported_dimensions: list[tuple[int,int]]=field(default_factory=lambda:[(1024,1280),(1280,1024),(1024,1024)])
@dataclass
class ProviderCapabilityDecision: provider: str='venice'; model: str=DEFAULT_IMAGE_MODEL; capabilities: ProviderImageCapabilities=field(default_factory=ProviderImageCapabilities); identity_consistency_level: str='best_effort_text_only'
@dataclass
class ImageExecutionPlan: action: str=ImageAction.NEW_GENERATION; billable: bool=True; enqueue_generation: bool=True; resend_source_job_id: int|None=None
@dataclass
class ResolvedImagePlan:
    plan_version: str=PLAN_VERSION; prompt_engine_version: str=PROMPT_ENGINE_VERSION; action: str=ImageAction.NEW_GENERATION; source_image_job_id: int|None=None; current_intent: dict=field(default_factory=dict); merged_intent: dict=field(default_factory=dict); scene: ResolvedField=field(default_factory=ResolvedField); location: ResolvedField=field(default_factory=ResolvedField); environment_type: ResolvedField=field(default_factory=ResolvedField); privacy: ResolvedField=field(default_factory=ResolvedField); support_surface: ResolvedField=field(default_factory=ResolvedField); required_objects: ResolvedField=field(default_factory=lambda: ResolvedField([])); excluded_objects: ResolvedField=field(default_factory=lambda: ResolvedField([])); activity: ResolvedField=field(default_factory=ResolvedField); pose: ResolvedField=field(default_factory=ResolvedField); wardrobe: ResolvedField=field(default_factory=ResolvedField); body_visibility: dict=field(default_factory=dict); safety_decision: SafetyDecision=field(default_factory=SafetyDecision); entitlement_decision: dict=field(default_factory=dict); composition: dict=field(default_factory=dict); camera: ResolvedField=field(default_factory=ResolvedField); lighting: ResolvedField=field(default_factory=ResolvedField); identity: dict=field(default_factory=dict); provider_capability_decision: ProviderCapabilityDecision=field(default_factory=ProviderCapabilityDecision); seed_strategy: dict=field(default_factory=dict); validation_results: dict=field(default_factory=lambda:{'errors':[],'warnings':[]})
@dataclass
class CompiledImagePrompt:
    positive_prompt: str; negative_prompt: str; provider_parameters: dict; sections: dict

SCENES={
 'bedroom':('home','private bedroom','private',['standing','bed','chair'],['bed','pillows'],[]), 'bed':('home','private bedroom with bed','private',['bed'],['bed','bedding','pillows'],[]), 'living_room':('home','living room','private',['sofa','chair','floor','standing'],['sofa'],['bed']), 'sofa':('home','living room with sofa','private',['sofa'],['sofa','cushions'],['bed']), 'bathroom':('home','bathroom','private',['standing','none'],['mirror','bathroom fixtures'],[]), 'mirror':('home','mirror area','private',['standing','none'],['mirror'],[]), 'hotel_room':('travel','hotel room','private',['bed','chair','standing'],['bed'],[]), 'car':('car','inside a car','private',['car_seat'],['car seat','dashboard'],[]), 'cafe':('cafe','cafe','public',['chair','standing'],['table','chair'],['bed']), 'restaurant':('restaurant','restaurant','public',['chair'],['table','chair'],['bed']), 'street':('outdoor','street','public',['standing'],['street background'],['bed','sofa']), 'park':('outdoor','park','public',['standing','floor'],['trees'],[]), 'beach':('outdoor','beach','public',['standing','floor'],['sand','sea'],[]), 'office':('workplace','office','public',['chair','standing'],['desk','chair'],['bed']), 'university':('campus','university','public',['chair','standing'],['campus background'],['bed']), 'metro':('transit','metro','public',['standing','chair'],['metro car'],['bed']), 'shop':('shop','shop','public',['standing'],['shop shelves'],['bed']), 'gym':('gym','gym','public',['standing','floor'],['gym equipment'],[])}
LEX={
 'image':['عکس','تصویر','بفرست','بساز','نشون'], 'resend':['دوباره بفرست','همونو بفرست','قبلی رو بفرست','باز بفرست'], 'variation':['یکی دیگه','یه دونه دیگه','واریاسیون','مثل قبلی'], 'refine':['این بار','ولی','اصلاح','بهتر','عوض کن'],
 'neg':['نه','نباشه','نمیخوام','بدون'], 'visibility':['معلوم','پیدا','نمایان','دیده'], 'medical':['درد','توضیح','در مورد','پزشکی','آناتومی'],
 'regions':{'breasts':['سینه','پستان'], 'buttocks':['باسن','کون'], 'genitals':['واژن','آلت','تناسلی'], 'upper_body':['بالا تنه'], 'lower_body':['پایین تنه'], 'full_body':['تمام بدن','لخت کامل','فول بادی']},
 'scenes':{'bed':['تخت','رختخواب'], 'bedroom':['اتاق خواب'], 'sofa':['مبل','کاناپه'], 'bathroom':['حمام'], 'mirror':['آینه'], 'hotel_room':['هتل'], 'car':['ماشین','خودرو'], 'cafe':['کافه'], 'restaurant':['رستوران'], 'street':['خیابان'], 'park':['پارک'], 'beach':['ساحل'], 'office':['دفتر','اداره'], 'university':['دانشگاه'], 'metro':['مترو'], 'shop':['فروشگاه','مغازه'], 'gym':['باشگاه']},
 'poses':{'reclining':['لم','دراز','تکیه'], 'seated':['نشست'], 'standing':['ایستاد'], 'walking':['راه','قدم'], 'lying':['خوابید']}}

def normalize_request_v2(text: str, *, user_id=None, chat_id=None, source_message_id=None) -> NormalizedImageRequest:
    n=normalize_and_tokenize(text); return NormalizedImageRequest(text or '', n.normalized, [t.__dict__ for t in n.tokens], user_id, chat_id, source_message_id)

def _contains_any(text, vals): return any(v in text for v in vals)
def _token_window_negated(tokens, idx): return any(tokens[j]['stem'] in {'نه','نمیخوام','نباش','بدون'} or tokens[j]['normalized'] in {'نباشه','نمیخوام'} for j in range(max(0,idx-4), idx))
def parse_image_intent(req: NormalizedImageRequest) -> ImageRequestIntent:
    text=req.normalized_text; tokens=req.tokens; stems=[t['stem'] for t in tokens]
    action=ImageAction.CHAT
    if _contains_any(text, LEX['resend']): action=ImageAction.RESEND_EXACT
    elif _contains_any(text, LEX['variation']): action=ImageAction.VARIATION
    elif _contains_any(text, LEX['refine']): action=ImageAction.REFINEMENT
    elif _contains_any(text, LEX['image']): action=ImageAction.NEW_GENERATION
    intent=ImageRequestIntent(is_image_request=action!=ImageAction.CHAT, route=ImageRouteDecisionV2(action, 'lexical_intent'), continuity=ContinuityIntent(action))
    nonvisual=_contains_any(text, LEX['medical']) and not _contains_any(text, LEX['image'])
    for key, variants in LEX['scenes'].items():
        if any(v in stems or v in text for v in variants):
            env, loc, privacy, surfaces, objs, inc = SCENES[key]; intent.scene=SceneIntent(key, surfaces[0] if len(surfaces)==1 else None, loc); break
    for key, variants in LEX['poses'].items():
        if any(v in s for s in stems for v in variants): intent.pose=PoseIntent(key); break
    for region, variants in LEX['regions'].items():
        found=[]
        for i,tok in enumerate(tokens):
            if tok['stem'] in variants or tok['normalized'] in variants:
                found.append((i,(tok['start'],tok['end'])))
        if found:
            reg=BodyRegionIntent(mentioned=True, explicit_current_request=True, source_spans=[s for _,s in found])
            for i,span in found:
                nearby=' '.join(x['normalized'] for x in tokens[i:i+5])
                if _contains_any(nearby, LEX['visibility']) and not nonvisual:
                    if _token_window_negated(tokens, i) or _contains_any(nearby, ['نباشه']): reg.visibility_negated=True; intent.explicit_exclusions.append(f'{region}_visible')
                    else: reg.visibility_requested=True
                    intent.visual_assertions.append(VisualAssertion(region,'visible','negative' if reg.visibility_negated else 'positive',span))
            intent.body_visibility.regions[region]=reg
    if not intent.is_image_request and intent.body_visibility.regions and not nonvisual: intent.is_image_request=True; intent.route=ImageRouteDecisionV2(ImageAction.NEW_GENERATION,'visual_body_intent')
    return intent

def find_eligible_source_image_context(db: Session, *, user_id:int, chat_id:int, ttl_minutes:int=30) -> ImageGenerationJob|None:
    cutoff=datetime.utcnow()-timedelta(minutes=ttl_minutes)
    return db.scalar(select(ImageGenerationJob).outerjoin(ImageGenerationArtifact).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status=='sent', ImageGenerationJob.sent_at>=cutoff, ((ImageGenerationArtifact.image_bytes.is_not(None)) | (ImageGenerationJob.archive_status.in_(['sent','disabled','skipped'])))).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))

def deserialize_resolved_plan(data: dict|None) -> ResolvedImagePlan|None:
    if not data: return None
    if data.get('plan_version') == PLAN_VERSION:
        return ResolvedImagePlan(**{k:v for k,v in data.items() if k in ResolvedImagePlan.__dataclass_fields__})
    return ResolvedImagePlan(plan_version='legacy-partial', prompt_engine_version=data.get('prompt_engine_version','legacy'), validation_results={'errors':[],'warnings':['legacy_partial_plan']}, composition={'composition_key':data.get('composition_key')}, environment_type=ResolvedField(data.get('environment_type'), Provenance.SOURCE_PLAN, inherited=True))

def resolve_seed(identity_seed:int, message_id:int, text:str, *, variation_index:int=0, source_seed:int|None=None):
    scene_seed=VENICE_SEED_MIN+(int(hashlib.sha256(f'{message_id}:{text}'.encode()).hexdigest(),16)%VENICE_SEED_MAX)
    offset=0 if not variation_index else VENICE_SEED_MIN+(int(hashlib.sha256(f'var:{variation_index}:{source_seed}'.encode()).hexdigest(),16)%VENICE_SEED_MAX)
    final=VENICE_SEED_MIN+((identity_seed+scene_seed+offset)%VENICE_SEED_MAX)
    if source_seed and final==source_seed: final = VENICE_SEED_MIN + (final % (VENICE_SEED_MAX-1))
    return {'identity_seed':identity_seed,'scene_seed':scene_seed,'variation_index':variation_index,'variation_seed_offset':offset,'final_provider_seed':final}

def merge_image_intent(current_intent: ImageRequestIntent, source_plan: ResolvedImagePlan|None=None, recent_context=None, memory_context=None, routine_context=None) -> dict:
    merged={}
    def setf(name, value, source, explicit=False, inherited=False):
        if value is None: return
        if name not in merged or explicit:
            merged[name]=ResolvedField(value, source, 1.0, explicit, inherited)
    setf('scene', current_intent.scene.scene_key, Provenance.EXPLICIT, True)
    setf('support_surface', current_intent.scene.support_surface, Provenance.EXPLICIT, True)
    setf('pose', current_intent.pose.pose, Provenance.EXPLICIT, True)
    if source_plan:
        for name in ['scene','support_surface','pose','wardrobe','location','environment_type','privacy']:
            f=getattr(source_plan, name, None)
            if isinstance(f, ResolvedField): setf(name, f.value, Provenance.SOURCE_PLAN, False, True)
    defaults={'scene':'bedroom','support_surface':'standing','pose':'standing','wardrobe':'tasteful casual clothing','lighting':'natural soft light','camera':'candid smartphone photo'}
    for k,v in defaults.items(): setf(k,v,Provenance.SYSTEM)
    return merged

def evaluate_safety_policy(intent: ImageRequestIntent) -> SafetyDecision:
    unsupported=[r for r,v in intent.body_visibility.regions.items() if v.visibility_requested and r in {'genitals'}]
    if unsupported: return SafetyDecision(PolicyDecision.DENY, 'unsupported_explicit_visibility', 'image_policy_unsupported_visibility')
    return SafetyDecision()

def ensure_visual_profile_v2(db: Session, user: User, profile: PartnerVisualProfile) -> PartnerVisualProfile:
    traits=profile.profile_json or {}; changed=False
    required=['face_shape','jaw','cheekbone','eyebrow_shape','eyebrow_spacing','eye_shape','eye_color','eye_spacing','nose_bridge','nose_width','nose_tip','lip_shape','lip_proportions','hairline','hair_length','hair_texture','hair_color','skin_tone','feature','build','height','grooming']
    banks={'cheekbone':['soft cheekbones','defined cheekbones'],'eyebrow_spacing':['balanced eyebrow spacing','slightly wide eyebrow spacing'],'eye_spacing':['balanced eye spacing'],'nose_bridge':['straight nose bridge'],'nose_width':['medium nose width'],'nose_tip':['soft rounded nose tip'],'lip_shape':['defined natural lips'],'lip_proportions':['balanced lip proportions'],'hairline':['natural hairline'],'hair_length':['shoulder-length hair'],'grooming':['well-kept realistic grooming']}
    for f in required:
        if not traits.get(f):
            choices=banks.get(f) or [traits.get({'jaw':'jaw','feature':'feature','build':'build','height':'height'}.get(f,f)) or f'natural {f.replace("_"," ")}']
            traits[f]=choices[int(hashlib.sha256(f'{profile.user_id}:{profile.base_seed}:{f}'.encode()).hexdigest(),16)%len(choices)]; changed=True
    if profile.base_seed < VENICE_SEED_MIN: profile.base_seed = resolve_seed(abs(profile.base_seed or profile.user_id), profile.user_id, 'identity')['identity_seed']; changed=True
    if changed or (profile.version or 1)<PROFILE_SCHEMA_VERSION:
        traits['schema_version']=PROFILE_SCHEMA_VERSION; traits['backfill_metadata']={'method':'deterministic_hash','at':datetime.utcnow().isoformat()}; profile.profile_json=traits; profile.version=PROFILE_SCHEMA_VERSION
        profile.face_description=profile.face_description or f"{traits['face_shape']}, {traits['jaw']}, {traits['cheekbone']}"; profile.updated_at=datetime.utcnow(); db.flush()
    return profile

def identity_descriptor_v2(profile: PartnerVisualProfile) -> dict:
    t=profile.profile_json or {}; d={'face_shape':t.get('face_shape'),'jaw_chin_geometry':t.get('jaw'),'cheekbone_structure':t.get('cheekbone') or profile.face_description,'eyebrow_shape':t.get('eyebrow_shape'),'eyebrow_spacing':t.get('eyebrow_spacing'),'eye_shape':t.get('eye_shape'),'eye_color':t.get('eye_color'),'eye_spacing':t.get('eye_spacing'),'nose_bridge':t.get('nose_bridge'),'nose_width':t.get('nose_width'),'nose_tip':t.get('nose_tip'),'lip_shape':t.get('lip_shape'),'lip_proportions':t.get('lip_proportions'),'hairline':t.get('hairline'),'hair_length':t.get('hair_length'),'hair_texture':t.get('hair_texture'),'hair_color':t.get('hair_color'),'skin_tone':t.get('skin_tone'),'stable_distinguishing_features':t.get('feature'),'body_build':t.get('build'),'height_impression':t.get('height'),'grooming_style_constraints':t.get('grooming')}
    return {k:(v if v not in (None,'','unknown','None','null') else f'natural {k.replace("_"," ")}') for k,v in d.items()}

def construct_resolved_plan(intent, merged, safety, profile, *, source_job=None, message_id=None, user_request=''):
    scene_key=merged['scene'].value; env, loc, priv, surfaces, objs, inc = SCENES.get(scene_key, SCENES['bedroom'])
    surface=merged['support_surface'];
    if surface.value == 'standing' and scene_key in SCENES and len(surfaces)==1: surface=ResolvedField(surfaces[0], Provenance.SYSTEM)
    variation_index=1 if intent.continuity.action==ImageAction.VARIATION else 0
    src_seed=getattr(source_job,'seed',None)
    seed=resolve_seed(profile.base_seed, message_id or 0, user_request, variation_index=variation_index, source_seed=src_seed)
    ident=identity_descriptor_v2(profile); action=str(intent.continuity.action)
    return ResolvedImagePlan(action=action, source_image_job_id=getattr(source_job,'id',None), current_intent=asdict(intent), merged_intent={k:asdict(v) for k,v in merged.items()}, scene=ResolvedField(scene_key, merged['scene'].source, explicit_current_request=merged['scene'].explicit_current_request, inherited=merged['scene'].inherited), location=ResolvedField(loc, Provenance.SYSTEM), environment_type=ResolvedField(env, Provenance.SYSTEM), privacy=ResolvedField(priv, Provenance.SYSTEM), support_surface=surface, required_objects=ResolvedField(objs), excluded_objects=ResolvedField(inc), pose=merged['pose'], wardrobe=merged['wardrobe'], body_visibility={k:asdict(v) for k,v in intent.body_visibility.regions.items()}, safety_decision=safety, entitlement_decision={'allow':safety.decision==PolicyDecision.ALLOW}, composition={'orientation':'portrait','width':DEFAULT_WIDTH,'height':DEFAULT_HEIGHT,'framing':intent.composition.framing or 'environmental three-quarter'}, camera=merged['camera'], lighting=merged['lighting'], identity={'descriptor':ident,'identity_fingerprint':hashlib.sha256(json.dumps(ident,sort_keys=True).encode()).hexdigest(),'schema_version':PROFILE_SCHEMA_VERSION}, seed_strategy=seed)

def validate_plan_invariants(plan: ResolvedImagePlan, *, source_job=None, user_id=None, chat_id=None) -> list[str]:
    errors=[]
    env, loc, priv, surfaces, objs, inc = SCENES.get(str(plan.scene.value), SCENES['bedroom'])
    if plan.support_surface.value not in surfaces: errors.append(InvariantCode.SUPPORT_SCENE_MISMATCH)
    if plan.pose.value in {'reclining','lying'} and plan.support_surface.value in {'standing','none','chair'}: errors.append(InvariantCode.POSE_SUPPORT_MISMATCH)
    if any(o not in plan.required_objects.value for o in objs): errors.append(InvariantCode.REQUIRED_OBJECT_MISSING)
    if plan.safety_decision.decision == PolicyDecision.DENY and plan.action not in {ImageAction.DENY, ImageAction.CHAT}: errors.append(InvariantCode.UNSUPPORTED_SAFETY_DOWNGRADE)
    if plan.action == ImageAction.RESEND_EXACT and plan.seed_strategy: errors.append(InvariantCode.RESEND_HAS_GENERATION)
    if plan.action == ImageAction.VARIATION and source_job and plan.seed_strategy.get('final_provider_seed') == source_job.seed: errors.append(InvariantCode.VARIATION_SEED_UNCHANGED)
    if source_job and (source_job.user_id != user_id or source_job.chat_id != chat_id): errors.append(InvariantCode.SOURCE_SCOPE_INVALID)
    bad=re.compile(r'\b(None|null|unknown)\b', re.I)
    if bad.search(json.dumps(plan.identity.get('descriptor',{}), ensure_ascii=False)): errors.append(InvariantCode.NULL_IDENTITY_DESCRIPTOR)
    if plan.composition.get('orientation')=='portrait' and plan.composition.get('width',0) > plan.composition.get('height',0): errors.append(InvariantCode.DIMENSION_ORIENTATION)
    plan.validation_results={'errors':[str(e) for e in errors], 'warnings':[]}
    return plan.validation_results['errors']

def compile_image_prompt(plan: ResolvedImagePlan) -> CompiledImagePrompt:
    ident=', '.join(f'{k}: {v}' for k,v in plan.identity.get('descriptor',{}).items())
    scene=f"{plan.location.value}; required objects: {', '.join(plan.required_objects.value or [])}"
    pose=f"{plan.pose.value} on/with {plan.support_surface.value}"
    sections={'identity':ident,'scene':scene,'support surface and pose':pose,'activity':str(plan.activity.value or 'natural candid activity'),'wardrobe':str(plan.wardrobe.value),'body visibility':json.dumps(plan.body_visibility, ensure_ascii=False),'composition':json.dumps(plan.composition),'lighting':str(plan.lighting.value),'realism/quality':'realistic candid smartphone photography','hard constraints':'no readable text, no watermark, coherent anatomy'}
    positive=' | '.join(f'{k}: {v}' for k,v in sections.items())
    neg_terms=['text','watermark','logo','bad anatomy'] + list(plan.excluded_objects.value or []) + [x for x in plan.current_intent.get('explicit_exclusions', [])]
    return CompiledImagePrompt(positive, ', '.join(dict.fromkeys(neg_terms)), {'width':plan.composition['width'],'height':plan.composition['height'],'seed':plan.seed_strategy.get('final_provider_seed')}, sections)

def validate_compiled_prompt(plan: ResolvedImagePlan, compiled: CompiledImagePrompt) -> list[str]:
    errors=[]
    for obj in plan.required_objects.value or []:
        if obj not in compiled.positive_prompt: errors.append(str(InvariantCode.REQUIRED_OBJECT_MISSING))
    for obj in plan.required_objects.value or []:
        if obj in compiled.negative_prompt: errors.append(str(InvariantCode.PROMPT_CONTRADICTION))
    return errors

def plan_to_json(plan: ResolvedImagePlan) -> dict: return asdict(plan)
