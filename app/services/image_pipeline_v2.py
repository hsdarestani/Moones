from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import StrEnum
from datetime import datetime, timedelta
import hashlib, json, re
import logging
logger=logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.llm.image_client import DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, VENICE_SEED_MIN, VENICE_SEED_MAX
from app.models.image_generation import ImageGenerationJob, ImageGenerationArtifact, PartnerVisualProfile
from app.models.user import User
from app.services.persian_normalization import normalize_and_tokenize
from app.services.image_semantic_lexicons import IMAGE_SEMANTIC_LEXICONS

PROMPT_ENGINE_VERSION = 'image-prompt-v1.9.0'
PLAN_VERSION = 'resolved-image-plan-v2.1'
PROFILE_SCHEMA_VERSION = 2

class ImageAction(StrEnum):
    NEW_GENERATION='new_generation'; VARIATION='variation'; REFINEMENT='refinement'; RESEND_EXACT='resend_exact'; DENY='deny'; CHAT='chat'

_ACTION_ALIASES={'new_generation':ImageAction.NEW_GENERATION,'generate_new':ImageAction.NEW_GENERATION,'refinement':ImageAction.REFINEMENT,'refine_previous':ImageAction.REFINEMENT,'variation':ImageAction.VARIATION,'resend_exact':ImageAction.RESEND_EXACT}
def canonical_image_action(action):
    return _ACTION_ALIASES.get(str(action), action)
class Provenance(StrEnum):
    EXPLICIT='explicit_current_request'; CORRECTION='correction'; EXCLUSION='explicit_current_exclusions'; SOURCE_PLAN='previous_visual_state'; RECENT='recent_conversation'; MEMORY='previous_visual_state'; ROUTINE='routine'; PROFILE='identity_profile'; SYSTEM='unspecified'; COMPATIBILITY_RESOLUTION='compatibility_resolution'; POSE_DERIVED='pose_derived'
class PolicyDecision(StrEnum):
    ALLOW='allow'; DENY='deny'; TRANSFORM='transform'
class ParseDisposition(StrEnum):
    COMPLETE='complete'; BEST_EFFORT='best_effort'; CLARIFICATION_REQUIRED='clarification_required'; DENY='deny'
class InvariantCode(StrEnum):
    EXPLICIT_POSE_SUPPORT_CONFLICT='explicit_pose_support_conflict'; EXPLICIT_OVERWRITTEN='explicit_current_field_overwritten'; SUPPORT_SCENE_MISMATCH='support_surface_scene_mismatch'; POSE_SUPPORT_MISMATCH='pose_support_surface_mismatch'; REQUIRED_OBJECT_MISSING='required_object_missing'; INCOMPATIBLE_OBJECT_PRESENT='incompatible_object_present'; UNSUPPORTED_SAFETY_DOWNGRADE='unsupported_safety_intent_not_downgraded'; RESEND_HAS_GENERATION='resend_has_generation_plan'; VARIATION_SEED_UNCHANGED='variation_seed_unchanged'; SOURCE_SCOPE_INVALID='source_job_scope_invalid'; SOURCE_STALE='source_job_stale'; IDENTITY_INCOMPLETE='identity_profile_incomplete'; NULL_IDENTITY_DESCRIPTOR='identity_descriptor_null_like'; DIMENSION_ORIENTATION='dimension_orientation_mismatch'; PROMPT_CONTRADICTION='prompt_contradiction'; MEANINGFUL_TOKENS_UNMATCHED='meaningful_tokens_unmatched'; ADULT_INTENT_CLASSIFIED_NORMAL='adult_intent_classified_as_normal'; SINGLE_SUBJECT_CONSTRAINT_MISSING='single_subject_constraint_missing'; UNEXPECTED_IDENTITY_FINGERPRINT_CHANGE='unexpected_identity_fingerprint_change'; PROFILE_SCHEMA_INCOMPLETE='profile_schema_version_claims_completeness_missing_fields'; GENERIC_FALLBACK_WITH_UNRESOLVED='generic_fallback_used_despite_meaningful_unresolved_terms'; INVENTED_FIELD='invented_prompt_field_without_resolved_provenance'; UNSPECIFIED_RENDERED='unspecified_field_rendered_as_concrete_detail'; IDENTITY_PASSTHROUGH='identity_attribute_in_request_passthrough'; SUBJECT_COUNT_MISMATCH='expected_subject_count_mismatch'


@dataclass
class SubjectIdentity:
    partner_name: object = None
    fictional_age: object = None
    gender_presentation: object = None
    face: object = None
    hair: object = None
    eyes: object = None
    skin: object = None
    body: object = None
    distinguishing_details: object = None
    identity_fingerprint: object = None

@dataclass
class CurrentVisualRequest:
    fields: dict = field(default_factory=dict)
    passthrough_visual_details: list[str] = field(default_factory=list)

@dataclass
class ConversationVisualContext:
    fields: dict = field(default_factory=dict)

@dataclass
class RoutineVisualContext:
    fields: dict = field(default_factory=dict)

@dataclass
class VisualContinuityContext:
    fields: dict = field(default_factory=dict)

@dataclass
class ResolvedScene:
    fields: dict = field(default_factory=dict)

@dataclass
class ResolvedComposition:
    fields: dict = field(default_factory=dict)

@dataclass
class ImagePromptContext:
    identity: SubjectIdentity
    current_request: CurrentVisualRequest
    conversation_context: ConversationVisualContext
    routine_context: RoutineVisualContext
    continuity_context: VisualContinuityContext
    resolved_scene: ResolvedScene
    composition: ResolvedComposition
    safety_constraints: list[str]
    provider_constraints: list[str]

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
class SemanticMatch:
    category: str
    canonical: str
    normalized_variant: str
    start: int
    end: int
    token_start_index: int
    token_end_index: int
    match_type: str
    confidence: float = 1.0

@dataclass
class TokenDebug:
    raw_token: str
    normalized_token: str
    stem: str
    suffixes: list[str]
    span: tuple[int, int]
    matched_semantic_category: str|None = None
    canonical_value: str|None = None
    unmatched_reason: str|None = None

@dataclass
class SpatialRelation:
    relation: str
    object: str|None = None
    source_span: tuple[int, int]|None = None

@dataclass
class ParseCoverage:
    matched_spans: list[tuple[int,int,str,str]] = field(default_factory=list)
    matched_token_indexes: list[int] = field(default_factory=list)
    unmatched_meaningful_tokens: list[str] = field(default_factory=list)
    unmatched_token_frequency: dict[str, int] = field(default_factory=dict)
    recognized_categories: list[str] = field(default_factory=list)
    semantic_matches: list[SemanticMatch] = field(default_factory=list)
    token_debug: list[TokenDebug] = field(default_factory=list)
    passthrough_visual_spans: list[str] = field(default_factory=list)
    critical_unresolved_spans: list[str] = field(default_factory=list)
    safety_critical_unresolved_spans: list[str] = field(default_factory=list)
    action_critical_unresolved_spans: list[str] = field(default_factory=list)
    source_critical_unresolved_spans: list[str] = field(default_factory=list)
    disposition: str = ParseDisposition.COMPLETE
    confidence: float = 1.0
    clarification_reason: str|None = None
    fallback_required: bool = False

class ContentClassification(StrEnum):
    NORMAL='normal'; SUGGESTIVE='suggestive'; LINGERIE='lingerie'; TOPLESS='topless'; FULL_NUDITY='full_nudity'; UNSUPPORTED_EXPLICIT_VISIBILITY='unsupported_explicit_visibility'; DENIED='denied'

@dataclass
class AdultImagePolicyContext:
    adult_enabled: bool = False
    soft_safety_enabled: bool = True
    normal_addon_owned: bool = False
    normal_addon_enabled: bool = False
    adult_addon_owned: bool = False
    adult_addon_enabled: bool = False
    fictional_partner_min_age: int = 18
    parsed_body_visibility: dict = field(default_factory=dict)
    nudity_level: str|None = None
    policy_version: str = 'image-safety-v2'

@dataclass
class BodyRegionIntent:
    mentioned: bool=False; visibility_requested: bool=False; visibility_negated: bool=False; framing_requested: bool=False; explicit_current_request: bool=False; source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class BodyVisibilityIntent:
    regions: dict[str, BodyRegionIntent]=field(default_factory=dict)
@dataclass
class SceneIntent: scene_key: str|None=None; support_surface: str|None=None; location: str|None=None; spatial_relations: list[SpatialRelation]=field(default_factory=list); source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class PoseIntent: pose: str|None=None; source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class WardrobeIntent: wardrobe: str|None=None; exclusions: list[str]=field(default_factory=list); explicit_current_request: bool=False
@dataclass
class CompositionIntent: orientation: str|None=None; framing: str|None=None; camera: str|None=None
@dataclass
class SecondarySubjectIntent:
    requested: bool=False; role: str|None=None; gender_presentation: str|None=None; fictional_adult_required: bool=True; source_spans: list[tuple[int,int]]=field(default_factory=list)
@dataclass
class ContinuityIntent: action: str=ImageAction.NEW_GENERATION; source_image_job_id: int|None=None
@dataclass
class IdentityIntent: consistency_level: str='best_effort_text_only'
@dataclass
class VisualAssertion: subject: str; attribute: str; polarity: str; source_span: tuple[int,int]; confidence: float=1.0
@dataclass
class ExpressionModifier: region: str|None; attribute: str; value: str; source_span: tuple[int,int]
@dataclass
class ImageRequestIntent:
    is_image_request: bool=False; route: ImageRouteDecisionV2|None=None; parse_coverage: ParseCoverage=field(default_factory=ParseCoverage); adult_intent: str|None=None; content_classification: str=ContentClassification.NORMAL; body_visibility: BodyVisibilityIntent=field(default_factory=BodyVisibilityIntent); scene: SceneIntent=field(default_factory=SceneIntent); pose: PoseIntent=field(default_factory=PoseIntent); wardrobe: WardrobeIntent=field(default_factory=WardrobeIntent); composition: CompositionIntent=field(default_factory=CompositionIntent); continuity: ContinuityIntent=field(default_factory=ContinuityIntent); identity: IdentityIntent=field(default_factory=IdentityIntent); visual_assertions: list[VisualAssertion]=field(default_factory=list); expression_modifiers: list[ExpressionModifier]=field(default_factory=list); explicit_exclusions: list[str]=field(default_factory=list); secondary_subject: SecondarySubjectIntent=field(default_factory=SecondarySubjectIntent); interaction: str|None=None; passthrough_visual_details: list[str]=field(default_factory=list)
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
class VisibilityTargets:
    face_visible: bool=True; upper_body_visible: bool=False; full_outfit_visible: bool=False; hands_visible: bool=False; held_object_visible: bool=False; environment_visible: bool=False
@dataclass
class StyleTargets:
    wardrobe: str|None=None; color: str|None=None; mood: str|None=None; formality: str|None=None; expression: str|None=None; attractiveness_constraints: list[str]=field(default_factory=list); realism_constraints: list[str]=field(default_factory=list)
@dataclass
class ContinuityTargets:
    preserve_identity: bool=True; preserve_only_face_identity: bool=False; preserve_previous_scene: bool=False; preserve_previous_outfit: bool=False; deliberately_vary_composition: bool=False
@dataclass
class VisualRequirements:
    requested_action: str=ImageAction.NEW_GENERATION; visibility_targets: VisibilityTargets=field(default_factory=VisibilityTargets); style_targets: StyleTargets=field(default_factory=StyleTargets); continuity_targets: ContinuityTargets=field(default_factory=ContinuityTargets); wardrobe_requested: bool=False; wardrobe_visibility_required: bool=False; framing_requirement: str='medium'; correction_signals: list[str]=field(default_factory=list); reason_codes: list[str]=field(default_factory=list)
@dataclass
class ContinuityPlan:
    preserve_face_identity: bool=True; preserve_scene: bool=False; preserve_outfit: bool=False; preserve_pose: bool=False; requested_variation_axes: list[str]=field(default_factory=list); forbidden_repetition_axes: list[str]=field(default_factory=list)

@dataclass
class ResolvedImagePlan:
    plan_version: str=PLAN_VERSION; prompt_engine_version: str=PROMPT_ENGINE_VERSION; action: str=ImageAction.NEW_GENERATION; source_image_job_id: int|None=None; current_intent: dict=field(default_factory=dict); merged_intent: dict=field(default_factory=dict); scene: ResolvedField=field(default_factory=ResolvedField); location: ResolvedField=field(default_factory=ResolvedField); environment_type: ResolvedField=field(default_factory=ResolvedField); privacy: ResolvedField=field(default_factory=ResolvedField); support_surface: ResolvedField=field(default_factory=ResolvedField); required_objects: ResolvedField=field(default_factory=lambda: ResolvedField([])); passthrough_visual_details: list[str]=field(default_factory=list); excluded_objects: ResolvedField=field(default_factory=lambda: ResolvedField([])); activity: ResolvedField=field(default_factory=ResolvedField); pose: ResolvedField=field(default_factory=ResolvedField); wardrobe: ResolvedField=field(default_factory=ResolvedField); body_visibility: dict=field(default_factory=dict); safety_decision: SafetyDecision=field(default_factory=SafetyDecision); entitlement_decision: dict=field(default_factory=dict); composition: dict=field(default_factory=dict); camera: ResolvedField=field(default_factory=ResolvedField); lighting: ResolvedField=field(default_factory=ResolvedField); identity: dict=field(default_factory=dict); provider_capability_decision: ProviderCapabilityDecision=field(default_factory=ProviderCapabilityDecision); seed_strategy: dict=field(default_factory=dict); visual_requirements: VisualRequirements=field(default_factory=VisualRequirements); continuity_plan: ContinuityPlan=field(default_factory=ContinuityPlan); request_fingerprint: str|None=None; validation_results: dict=field(default_factory=lambda:{'errors':[],'warnings':[]})
@dataclass
class ResolvedPoseSupport:
    pose: ResolvedField
    support_surface: ResolvedField
    changed: bool = False
    reason_code: str|None = None
    provenance: str|None = None

@dataclass
class CompiledImagePrompt:
    positive_prompt: str; negative_prompt: str; provider_parameters: dict; sections: dict


POSE_SUPPORT_COMPATIBILITY={
    'standing': {'standing','floor','none'},
    'seated': {'chair','sofa','bed','floor','car_seat'},
    'reclining': {'sofa','bed','floor'},
    'lying': {'bed','sofa','floor'},
    'walking': {'standing','floor'},
}
POSE_SUPPORT_PREFERRED={
    'living_room': {'reclining':'sofa','lying':'sofa','seated':'sofa','standing':'standing','walking':'floor'},
    'sofa': {'reclining':'sofa','lying':'sofa','seated':'sofa'},
    'bedroom': {'reclining':'bed','lying':'bed','seated':'bed'},
    'bed': {'reclining':'bed','lying':'bed','seated':'bed'},
    'hotel_room': {'reclining':'bed','lying':'bed','seated':'bed'},
    'park': {'reclining':'floor','lying':'floor','seated':'floor','walking':'floor'},
    'beach': {'reclining':'floor','lying':'floor','seated':'floor','walking':'floor'},
    'gym': {'reclining':'floor','lying':'floor','seated':'floor'},
}
SUPPORT_SCENE_HINT={'sofa':'sofa','bed':'bed','car_seat':'car','chair':None,'floor':None,'standing':None,'none':None}

SCENES={
 'bedroom':('home','private bedroom','private',['standing','bed','chair'],['bed','pillows'],[]), 'bed':('home','private bedroom with bed','private',['bed'],['bed','bedding','pillows'],[]), 'living_room':('home','living room','private',['sofa','chair','floor','standing'],['sofa'],['bed']), 'sofa':('home','living room with sofa','private',['sofa'],['sofa','cushions'],['bed']), 'bathroom':('home','bathroom','private',['standing','none'],['mirror','bathroom fixtures'],[]), 'mirror':('home','mirror area','private',['standing','none'],['mirror'],[]), 'hotel_room':('travel','hotel room','private',['bed','chair','standing'],['bed'],[]), 'car':('car','inside a car','private',['car_seat'],['car seat','dashboard'],[]), 'cafe':('cafe','cafe','public',['chair','standing'],['table','chair'],['bed']), 'restaurant':('restaurant','restaurant','public',['chair'],['table','chair'],['bed']), 'street':('outdoor','street','public',['standing'],['street background'],['bed','sofa']), 'park':('outdoor','park','public',['standing','floor'],['trees'],[]), 'beach':('outdoor','beach','public',['standing','floor'],['sand','sea'],[]), 'office':('workplace','office','public',['chair','standing'],['desk','chair'],['bed']), 'university':('campus','university','public',['chair','standing'],['campus background'],['bed']), 'metro':('transit','metro','public',['standing','chair'],['metro car'],['bed']), 'shop':('shop','shop','public',['standing'],['shop shelves'],['bed']), 'gym':('gym','gym','public',['standing','floor'],['gym equipment'],[])}
def _lex_entries(*names):
    out=[]
    for name in names:
        out.extend(IMAGE_SEMANTIC_LEXICONS.get(name, ()))
    return out

def _variants(entry):
    return tuple(entry.persian_variants) + tuple(entry.colloquial_variants)

def _canonical_token(value: str) -> str:
    v=(value or '').replace('‌','').replace('ي','ی').replace('ك','ک')
    suffixes=('مون','تون','شون','ام','ات','اش','مو','تو','شو','رو','را')
    for suf in suffixes:
        if len(v)>len(suf)+1 and v.endswith(suf):
            v=v[:-len(suf)]
            break
    if v in {'مم','ممه'}: return 'ممه'
    if v in {'سین','سين'}: return 'سینه'
    if v == 'کس': return 'کص'
    if v in {'کاملاً','کاملا'}: return 'کاملا'
    if v in {'تمامقد','تمام‌قد'}: return 'تمامقد'
    return v

def _normalized_variant(value: str) -> str:
    return _canonical_token((value or '').replace(' ', '').replace('‌', ''))


def _semantic_matches(entries, tokens, text: str) -> list[SemanticMatch]:
    candidates=[]
    occupied=set()
    norm_stream=[_canonical_token(t.get('normalized','')) for t in tokens]
    stem_stream=[_canonical_token(t.get('stem') or t.get('normalized','')) for t in tokens]
    for entry in entries:
        variants=[v for v in _variants(entry) if v]
        for i,t in enumerate(tokens):
            raw=_canonical_token(t.get('normalized') or '')
            stem=_canonical_token(t.get('stem') or '')
            vals=[_normalized_variant(v) for v in variants]
            if raw in vals:
                candidates.append(SemanticMatch(entry.category, entry.canonical, raw, t['start'], t['end'], i, i, 'exact_token', 1.0))
            elif entry.suffix_stemming_allowed and stem in vals:
                candidates.append(SemanticMatch(entry.category, entry.canonical, stem, t['start'], t['end'], i, i, 'canonical_stem', 0.95))
        for v in variants:
            parts=[_canonical_token(x) for x in normalize_and_tokenize(v).normalized.split() if x]
            if len(parts) <= 1: continue
            for i in range(0, len(tokens)-len(parts)+1):
                if norm_stream[i:i+len(parts)] == parts or stem_stream[i:i+len(parts)] == parts:
                    candidates.append(SemanticMatch(entry.category, entry.canonical, ' '.join(parts), tokens[i]['start'], tokens[i+len(parts)-1]['end'], i, i+len(parts)-1, 'phrase', 1.0))
        if entry.regex:
            for m in re.finditer(entry.regex, text):
                idx=[i for i,t in enumerate(tokens) if not (t['end'] <= m.start() or t['start'] >= m.end())]
                if idx: candidates.append(SemanticMatch(entry.category, entry.canonical, m.group(0), m.start(), m.end(), min(idx), max(idx), 'regex', 0.9))
    candidates.sort(key=lambda m: (-(m.token_end_index-m.token_start_index+1), -m.confidence, m.start, m.category, m.canonical))
    chosen=[]
    for m in candidates:
        rng=set(range(m.token_start_index, m.token_end_index+1))
        if rng & occupied: continue
        chosen.append(m); occupied |= rng
    return sorted(chosen, key=lambda m: (m.start, m.end, m.category))

def normalize_request_v2(text: str, *, user_id=None, chat_id=None, source_message_id=None) -> NormalizedImageRequest:
    n=normalize_and_tokenize(text); return NormalizedImageRequest(text or '', n.normalized, [t.__dict__ for t in n.tokens], user_id, chat_id, source_message_id)

def _contains_any(text, vals): return any(v in text for v in vals)
def _token_window_negated(tokens, idx): return any(_canonical_token(tokens[j].get('stem') or tokens[j].get('normalized')) in {'نه','نمیخوام','نباش','بدون'} or tokens[j]['normalized'] in {'نباشه','نمیخوام'} for j in range(max(0,idx-4), min(len(tokens), idx+4)))

def _record_match(coverage, match_or_span, category=None, canonical=None):
    if isinstance(match_or_span, SemanticMatch):
        m=match_or_span; span=(m.start, m.end); category=m.category; canonical=m.canonical
        coverage.semantic_matches.append(m)
        for i in range(m.token_start_index, m.token_end_index+1):
            if i not in coverage.matched_token_indexes: coverage.matched_token_indexes.append(i)
    else:
        span=match_or_span
    coverage.matched_spans.append((span[0], span[1], category, canonical))
    if category not in coverage.recognized_categories: coverage.recognized_categories.append(category)


def _first_match(entries, tokens, text):
    ms=_semantic_matches(entries, tokens, text)
    return ms[0] if ms else None



_CONTROL_CHARS_RE=re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
def sanitize_passthrough_visual_detail(value: str, *, max_len:int=120) -> str:
    value=_CONTROL_CHARS_RE.sub('', value or '').replace('ي','ی').replace('ك','ک')
    value=re.sub(r'\s+', ' ', value).strip(' ،,.؛;:')
    return value[:max_len].strip()

def passthrough_details_hash(details: list[str]) -> str:
    normalized=[sanitize_passthrough_visual_detail(x) for x in details or [] if sanitize_passthrough_visual_detail(x)]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]

def _collect_passthrough_spans(req: NormalizedImageRequest, coverage: ParseCoverage, stop: set[str], *, max_total:int=360) -> list[str]:
    spans=[]; current=[]; current_end=-1; matched=set(coverage.matched_token_indexes)
    for i,t in enumerate(req.tokens):
        can=_canonical_token(t.get('stem') or t.get('normalized'))
        routeish=can in stop or can.isdigit() or len(can)<=1
        if i not in matched and not routeish:
            if current and t['start'] - current_end > 2:
                spans.append(sanitize_passthrough_visual_detail(req.normalized_text[current[0]['start']:current[-1]['end']]))
                current=[]
            current.append(t); current_end=t['end']
        elif current:
            spans.append(sanitize_passthrough_visual_detail(req.normalized_text[current[0]['start']:current[-1]['end']]))
            current=[]
    if current: spans.append(sanitize_passthrough_visual_detail(req.normalized_text[current[0]['start']:current[-1]['end']]))
    out=[]; total=0
    for span in spans:
        if not span: continue
        remaining=max_total-total
        if remaining <= 0: break
        clipped=span[:remaining].strip()
        if clipped: out.append(clipped); total += len(clipped)
    return out

def classify_unresolved_spans(intent: ImageRequestIntent, req: NormalizedImageRequest) -> ParseCoverage:
    text=req.normalized_text; cov=intent.parse_coverage
    def add(kind, reason, span):
        val=sanitize_passthrough_visual_detail(span)
        if val and val not in cov.critical_unresolved_spans: cov.critical_unresolved_spans.append(val)
        if kind == 'safety' and val not in cov.safety_critical_unresolved_spans: cov.safety_critical_unresolved_spans.append(val)
        if kind == 'action' and val not in cov.action_critical_unresolved_spans: cov.action_critical_unresolved_spans.append(val)
        if kind == 'source' and val not in cov.source_critical_unresolved_spans: cov.source_critical_unresolved_spans.append(val)
        cov.clarification_reason=reason
    if re.search(r'(عکس جدید|جدید).*(قبلی|همون)|(?:قبلی|همون).*(عکس جدید|جدید)', text): add('action','image_action_ambiguous', text)
    if intent.continuity.action in {ImageAction.REFINEMENT, ImageAction.VARIATION, ImageAction.RESEND_EXACT} and not intent.continuity.source_image_job_id and not cov.passthrough_visual_spans and re.search(r'(همون|قبلی|عوض|تغییر|مثل قبلی)', text): add('source','image_source_ambiguous', text)
    if re.search(r'(یه|یک) نفر.*(سه|۳) نفر|(سه|۳) نفر.*(یه|یک) نفر', text): add('action','image_composition_conflict', text)
    if re.search(r'(بچه|کودک|نوجوون|نوجوان|زیر ?سن|کم سن).*(بزرگسال|فرقی نداره)|(بزرگسال).*(بچه|کودک|نوجوون|نوجوان|فرقی نداره)', text): add('safety','image_safety_detail_ambiguous', text)
    if cov.critical_unresolved_spans:
        cov.disposition=ParseDisposition.CLARIFICATION_REQUIRED; cov.confidence=min(cov.confidence, .45)
    elif intent.is_image_request and cov.passthrough_visual_spans:
        cov.disposition=ParseDisposition.BEST_EFFORT; cov.confidence=min(cov.confidence, .86)
    elif intent.is_image_request:
        cov.disposition=ParseDisposition.COMPLETE; cov.confidence=max(cov.confidence, .95)
    return cov

def unmatched_tokens_are_harmless_generic_request_terms(intent: ImageRequestIntent) -> bool:
    harmless={'ببینمت','ببینم','بذار','میخوام','میخو','خودتو','نشونم','نشون','نشان','بده','خب'}
    return all(tok in harmless for tok in (intent.parse_coverage.unmatched_meaningful_tokens or []))


def has_unresolved_visual_or_safety_signals(intent: ImageRequestIntent) -> bool:
    return bool(intent.parse_coverage.critical_unresolved_spans)

def parse_image_intent(req: NormalizedImageRequest) -> ImageRequestIntent:
    text=req.normalized_text; tokens=req.tokens
    action=ImageAction.CHAT; reason='lexical_intent'; coverage=ParseCoverage()
    # Phrase-level route first.
    for route, cat, act in [('resend_phrases','continuity',ImageAction.RESEND_EXACT),('variation_phrases','continuity',ImageAction.VARIATION),('refinement_phrases','continuity',ImageAction.REFINEMENT)]:
        m=_first_match(IMAGE_SEMANTIC_LEXICONS[route], tokens, text)
        if m:
            action=act; _record_match(coverage, m); break
    if action == ImageAction.CHAT:
        m=_first_match(IMAGE_SEMANTIC_LEXICONS['image_request_verbs'], tokens, text)
        if m: action=ImageAction.NEW_GENERATION; _record_match(coverage, m)
        elif 'عکص' in text:
            action=ImageAction.NEW_GENERATION
            for i,tok in enumerate(tokens):
                if tok.get('normalized') == 'عکص':
                    _record_match(coverage, SemanticMatch('route','request_image_typo','عکص',tok['start'],tok['end'],i,i,'typo',0.8)); break
    intent=ImageRequestIntent(is_image_request=action!=ImageAction.CHAT, route=ImageRouteDecisionV2(action, reason), continuity=ContinuityIntent(action), parse_coverage=coverage)
    nonvisual=bool(_first_match(IMAGE_SEMANTIC_LEXICONS['medical_nonvisual_context'], tokens, text)) and action==ImageAction.CHAT
    if nonvisual: coverage.recognized_categories.append('medical/nonvisual context')
    matched_by_token={i:[] for i in range(len(tokens))}
    for key, target, attr in [('scene_location','scene','scene_key'),('support_surfaces','support_surface','support_surface'),('pose','pose','pose')]:
        for m in _semantic_matches(IMAGE_SEMANTIC_LEXICONS[key], tokens, text):
            if target=='scene' and not intent.scene.scene_key: intent.scene.scene_key=m.canonical; intent.scene.source_spans.append((m.start,m.end))
            elif target=='support_surface' and not intent.scene.support_surface: intent.scene.support_surface=m.canonical
            elif target=='pose' and not intent.pose.pose: intent.pose.pose=m.canonical; intent.pose.source_spans.append((m.start,m.end))
            _record_match(coverage,m)
            break
    rel_map={'روی':'on','توی':'inside','داخل':'inside','کنار':'beside','پشت':'behind','جلوی':'in_front_of','زیر':'under'}
    for i,t in enumerate(tokens[:-1]):
        rel=rel_map.get(t.get('normalized')) or rel_map.get(_canonical_token(t.get('normalized')))
        if not rel: continue
        obj_match=next((m for m in coverage.semantic_matches if m.token_start_index==i+1 and m.category in {'scene','support_surface'}), None)
        if obj_match:
            intent.scene.spatial_relations.append(SpatialRelation(rel, obj_match.canonical, (t['start'], obj_match.end)))
            if obj_match.canonical == 'sofa': intent.scene.scene_key = intent.scene.scene_key or 'sofa'; intent.scene.support_surface = intent.scene.support_surface or 'sofa'
            _record_match(coverage, SemanticMatch('spatial_relation', rel, t['normalized'], t['start'], t['end'], i, i, 'exact_token', 1.0))
    for key in ['activity','interactions','secondary_subject_roles','camera_framing','wardrobe','adult_intent','body_visibility','exclusions_corrections','expression_modifiers','conversational_image_request_terms']:
        for m in _semantic_matches(IMAGE_SEMANTIC_LEXICONS[key], tokens, text):
            _record_match(coverage,m)
            if key=='interactions':
                intent.interaction=m.canonical
                if m.canonical in {'kiss','hug','holding_hands'} and intent.content_classification == ContentClassification.NORMAL:
                    intent.content_classification=ContentClassification.SUGGESTIVE
            if key=='secondary_subject_roles':
                intent.secondary_subject.requested=True; intent.secondary_subject.role=m.canonical; intent.secondary_subject.source_spans.append((m.start,m.end))
            if key=='wardrobe':
                intent.wardrobe=WardrobeIntent(m.canonical, explicit_current_request=True)
                if m.canonical == 'lingerie':
                    intent.adult_intent='lingerie'; intent.content_classification=ContentClassification.LINGERIE
            if key=='camera_framing':
                if m.canonical in {'selfie','mirror_selfie'}:
                    intent.composition.camera=m.canonical
                if not (m.canonical == 'closeup' and any(tok.get('normalized') == 'بدون' for tok in tokens[max(0,m.token_start_index-3):m.token_start_index+1])):
                    intent.composition.framing=m.canonical
            if key=='adult_intent':
                negated_exclusion = (m.canonical == 'unsupported_explicit_visibility' and any(tok.get('normalized') == 'بدون' for tok in tokens[max(0,m.token_start_index-2):m.token_start_index+1]))
                if negated_exclusion:
                    intent.explicit_exclusions.append('genital_closeup')
                else:
                    intent.adult_intent=m.canonical
                    if m.canonical == 'suggestive': intent.content_classification=ContentClassification.SUGGESTIVE
                    elif m.canonical == 'topless': intent.content_classification=ContentClassification.TOPLESS
                    elif m.canonical == 'full_nudity': intent.content_classification=ContentClassification.FULL_NUDITY
                    elif m.canonical == 'unsupported_explicit_visibility': intent.content_classification=ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
            if key=='activity':
                if not intent.pose.pose and m.canonical in {'walking'}: intent.pose.pose=m.canonical
            if key=='expression_modifiers':
                region = 'lips' if m.canonical in {'pursed_lips','smile'} else ('eyes' if m.canonical in {'eyes_closed','eyes_open'} else ('hair' if m.canonical in {'hair_loose','hair_tied'} else None))
                val = {'pursed_lips':'pursed','smile':'smile','frown':'frown','eyes_closed':'closed','eyes_open':'open','hair_loose':'loose','hair_tied':'tied'}.get(m.canonical, m.canonical)
                intent.expression_modifiers.append(ExpressionModifier(region, 'shape/expression', val, (m.start,m.end)))
    visibility_heads={'ببین','ببینم','نشون','نشان','معلوم','دیده','پیدا'}
    visibility_verbs=visibility_heads
    for i,t in enumerate(tokens):
        can=_canonical_token(t.get('stem') or t.get('normalized'))
        nxt=_canonical_token(tokens[i+1].get('stem') or tokens[i+1].get('normalized')) if i+1 < len(tokens) else ''
        if can in {'معلوم','مشخص','دیده','پیدا'} or can in {'ببین','ببینم'} or (can in {'نشون','نشان'} and nxt in {'بده','بد','داد'}):
            _record_match(coverage, SemanticMatch('visibility_request', can, t['normalized'], t['start'], t['end'], i, i, 'canonical_stem', .95))
    body_alias={'ممه':'breasts','سینه':'breasts','پستان':'breasts','کون':'buttocks','باسن':'buttocks','کص':'genitals','کس':'genitals','واژن':'genitals','آلت':'genitals','تناسلی':'genitals','بازو':'arms','ساعد':'forearms','دست':'hands','لب':'lips','دهان':'mouth','صورت':'face','گونه':'cheeks','چشم':'eyes','مو':'hair'}
    for i,t in enumerate(tokens):
        canon=_canonical_token(t.get('stem') or t.get('normalized') or '')
        region=body_alias.get(canon)
        if not region: continue
        reg=intent.body_visibility.regions.setdefault(region, BodyRegionIntent(mentioned=True, explicit_current_request=True))
        reg.mentioned=True; reg.explicit_current_request=True; reg.source_spans.append((t['start'],t['end']))
        _record_match(coverage, SemanticMatch('body_region', region, canon, t['start'], t['end'], i, i, 'canonical_stem', 1.0))
        nearby=' '.join(x['normalized'] for x in tokens[max(0,i-2):i+5])
        asks_visibility=any(m.category=='visibility_request' and abs(m.token_start_index-i)<=4 for m in coverage.semantic_matches) or any(m.category=='body_visibility' and abs(m.token_start_index-i)<=4 for m in coverage.semantic_matches) or intent.is_image_request
        if asks_visibility and not nonvisual:
            if _token_window_negated(tokens, i): reg.visibility_negated=True; intent.explicit_exclusions.append(f'{region}_visible')
            else: reg.visibility_requested=True
            intent.visual_assertions.append(VisualAssertion(region,'visible','negative' if reg.visibility_negated else 'positive',(t['start'],t['end'])))
    if intent.body_visibility.regions and not nonvisual:
        intent.is_image_request=True; intent.route=ImageRouteDecisionV2(ImageAction.NEW_GENERATION,'visual_body_intent') if action==ImageAction.CHAT else intent.route
    # Consume image-of/framing relation: از + recognized visual region.
    for i,t in enumerate(tokens[:-1]):
        if _canonical_token(t.get('normalized')) != 'از': continue
        nxt=next((m for m in coverage.semantic_matches if m.token_start_index==i+1 and m.category=='body_region'), None)
        if nxt:
            reg=intent.body_visibility.regions.setdefault(nxt.canonical, BodyRegionIntent(mentioned=True, explicit_current_request=True))
            reg.framing_requested=True
            _record_match(coverage, SemanticMatch('image_subject_relation', 'from', t['normalized'], t['start'], t['end'], i, i, 'exact_token', 1.0))
    if intent.adult_intent == 'full_nudity':
        for r in ('breasts','buttocks','full_body'):
            intent.body_visibility.regions.setdefault(r, BodyRegionIntent(True, True, False, False, True, []))
        intent.content_classification=ContentClassification.FULL_NUDITY
    elif intent.adult_intent == 'topless':
        intent.body_visibility.regions.setdefault('breasts', BodyRegionIntent(True, True, False, False, True, []))
        intent.content_classification=ContentClassification.TOPLESS
    elif intent.adult_intent == 'unsupported_explicit_visibility':
        intent.content_classification=ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
    elif any(r=='genitals' and v.visibility_requested for r,v in intent.body_visibility.regions.items()): intent.content_classification=ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
    elif intent.content_classification == ContentClassification.NORMAL and any(v.visibility_requested for v in intent.body_visibility.regions.values()): intent.content_classification=ContentClassification.SUGGESTIVE
    if intent.interaction in {'kiss','hug','holding_hands'} and intent.secondary_subject.requested and intent.content_classification == ContentClassification.NORMAL:
        intent.content_classification=ContentClassification.SUGGESTIVE
    stop={'عکس','بده','بد','بفرست','یه','یک','من','تو','باش','باشه','باشی','بشه','توش','رو','را','و','از','با','این','بار','قبلی','همون','دیگه','مثل','داده','درد','دار','توضیح','پزشکی','شماره','کشیده','بزن','معمولی','خودت','عوض','کن'}
    freq={}
    matched=set(coverage.matched_token_indexes)
    for i,t in enumerate(tokens):
        can=_canonical_token(t.get('stem') or t.get('normalized'))
        cat=next((m for m in coverage.semantic_matches if m.token_start_index <= i <= m.token_end_index), None)
        reason=None
        if i not in matched and len(can)>1 and can not in stop and not can.isdigit(): freq[can]=freq.get(can,0)+1; reason='unmatched_meaningful_token'
        coverage.token_debug.append(TokenDebug(t.get('original',''), t.get('normalized',''), t.get('stem',''), list(t.get('suffixes') or []), (t['start'],t['end']), getattr(cat,'category',None), getattr(cat,'canonical',None), reason))
    coverage.unmatched_token_frequency=freq
    coverage.unmatched_meaningful_tokens=list(freq.keys())
    coverage.passthrough_visual_spans=_collect_passthrough_spans(req, coverage, stop) if intent.is_image_request else []
    intent.passthrough_visual_details=list(dict.fromkeys(coverage.passthrough_visual_spans))
    coverage.fallback_required=False
    coverage.confidence=1.0
    classify_unresolved_spans(intent, req)
    return intent

def source_job_is_retrievable(job: ImageGenerationJob, *, user_id:int, chat_id:int, ttl_minutes:int=30) -> bool:
    if not job or job.user_id != user_id or job.chat_id != chat_id or job.status != 'sent': return False
    if job.sent_at and job.sent_at < datetime.utcnow()-timedelta(minutes=ttl_minutes): return False
    if any(a.image_bytes for a in getattr(job, 'artifacts', []) or []): return True
    return False

def find_eligible_source_image_context(db: Session, *, user_id:int, chat_id:int, ttl_minutes:int=30) -> ImageGenerationJob|None:
    cutoff=datetime.utcnow()-timedelta(minutes=ttl_minutes)
    return db.scalar(select(ImageGenerationJob).outerjoin(ImageGenerationArtifact).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.chat_id==chat_id, ImageGenerationJob.status=='sent', ImageGenerationJob.sent_at>=cutoff, (ImageGenerationArtifact.image_bytes.is_not(None))).order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(1))

def _restore_dataclass(cls, value):
    if value is None or isinstance(value, cls): return value
    if not isinstance(value, dict): return value
    kwargs={}
    for k in cls.__dataclass_fields__:
        if k in value: kwargs[k]=value[k]
    obj=cls(**kwargs)
    if cls is ResolvedImagePlan:
        for name in ['scene','location','environment_type','privacy','support_surface','required_objects','excluded_objects','activity','pose','wardrobe','camera','lighting']:
            setattr(obj, name, _restore_dataclass(ResolvedField, getattr(obj, name)))
        obj.safety_decision=_restore_dataclass(SafetyDecision, obj.safety_decision)
        obj.provider_capability_decision=_restore_dataclass(ProviderCapabilityDecision, obj.provider_capability_decision)
        if isinstance(obj.provider_capability_decision.capabilities, dict):
            obj.provider_capability_decision.capabilities=_restore_dataclass(ProviderImageCapabilities, obj.provider_capability_decision.capabilities)
    return obj

def deserialize_resolved_plan(data: dict|None) -> ResolvedImagePlan|None:
    if not data: return None
    if data.get('plan_version') == PLAN_VERSION:
        return _restore_dataclass(ResolvedImagePlan, data)
    return ResolvedImagePlan(plan_version='legacy-partial', prompt_engine_version=data.get('prompt_engine_version','legacy'), validation_results={'errors':[],'warnings':['legacy_partial_plan']}, composition={'composition_key':data.get('composition_key')}, environment_type=ResolvedField(data.get('environment_type'), Provenance.SOURCE_PLAN, inherited=True))

def resolve_image_seed(profile_base_seed:int, action:str, request_fingerprint, continuity_source_job=None, variation_axes=None):
    identity_seed=int(profile_base_seed or 0)
    source_seed=getattr(continuity_source_job, 'final_provider_seed', None) or getattr(continuity_source_job, 'seed', None)
    source_job_id=getattr(continuity_source_job, 'id', None)
    action=canonical_image_action(action)
    fp=str(request_fingerprint or '')
    axes=':'.join(variation_axes or [])
    seed_family=hashlib.sha256(f'identity:{identity_seed}'.encode()).hexdigest()[:12]
    if action == ImageAction.RESEND_EXACT:
        out={'identity_seed':identity_seed,'scene_seed':None,'variation_index':0,'variation_seed_offset':0,'final_provider_seed':None,'continuity_source_job_id':source_job_id,'continuity_mode':'resend_exact','seed_strategy':'reuse_prior_artifact','seed_family':seed_family,'request_fingerprint':fp}; logger.info('IMAGE_SEED_STRATEGY_SELECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', None, source_job_id, action, out['continuity_mode'], [out['seed_strategy']]); return out
    if action == ImageAction.REFINEMENT and source_seed is not None:
        scene_seed=VENICE_SEED_MIN+(int(hashlib.sha256(f'refine:{source_seed}:{fp}'.encode()).hexdigest(),16)%VENICE_SEED_MAX)
        final=VENICE_SEED_MIN+((int(source_seed)*11 + scene_seed) % VENICE_SEED_MAX)
        return {'identity_seed':identity_seed,'scene_seed':scene_seed,'variation_index':0,'variation_seed_offset':scene_seed,'final_provider_seed':final,'continuity_source_job_id':source_job_id,'continuity_mode':'refine_previous','seed_strategy':'continuity_biased_refinement','seed_family':seed_family,'request_fingerprint':fp}
    if action == ImageAction.VARIATION and source_seed is not None:
        offset=VENICE_SEED_MIN+(int(hashlib.sha256(f'variation:{source_seed}:{fp}:{axes}'.encode()).hexdigest(),16)%VENICE_SEED_MAX)
        final=VENICE_SEED_MIN+((int(source_seed) + offset + 9973) % VENICE_SEED_MAX)
        if final == source_seed: final = VENICE_SEED_MIN + ((final + 7919) % (VENICE_SEED_MAX-1))
        return {'identity_seed':identity_seed,'scene_seed':source_seed,'variation_index':1,'variation_seed_offset':offset,'final_provider_seed':final,'continuity_source_job_id':source_job_id,'continuity_mode':'variation','seed_strategy':'identity_preserving_variation_offset','seed_family':seed_family,'request_fingerprint':fp}
    scene_seed=VENICE_SEED_MIN+(int(hashlib.sha256(f'new-composition:{identity_seed}:{fp}:{axes}:{source_job_id}'.encode()).hexdigest(),16)%VENICE_SEED_MAX)
    final=VENICE_SEED_MIN+((identity_seed*3+scene_seed) % VENICE_SEED_MAX)
    out={'identity_seed':identity_seed,'scene_seed':scene_seed,'variation_index':0,'variation_seed_offset':0,'final_provider_seed':final,'continuity_source_job_id':source_job_id,'continuity_mode':'generate_new','seed_strategy':'stable_identity_new_composition_branch','seed_family':seed_family,'request_fingerprint':fp}; logger.info('IMAGE_SEED_STRATEGY_SELECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', None, source_job_id, action, out['continuity_mode'], [out['seed_strategy']]); return out

def resolve_seed(identity_seed:int, message_id:int, text:str, *, variation_index:int=0, source_seed:int|None=None):
    action=ImageAction.VARIATION if variation_index else ImageAction.NEW_GENERATION
    previous=type('PreviousSeed', (), {'id': None, 'seed': source_seed, 'final_provider_seed': source_seed})() if source_seed is not None else None
    return resolve_image_seed(identity_seed, action, hashlib.sha256(f'{message_id}:{text}'.encode()).hexdigest()[:16], previous, ['legacy_variation'] if variation_index else None)

def _log_prompt_field(event: str, *, user_id=None, field: str, provenance: str, action: str):
    import logging
    logging.getLogger(__name__).info('%s user_id=%s field=%s provenance=%s action=%s', event, user_id, field, provenance, action)

PROMPT_RESOLVED_FIELDS=('scene','location','activity','pose','support_surface','wardrobe','expression','camera','framing','lighting','time_of_day','held_objects','visible_objects')


def _field(value=None, source=Provenance.SYSTEM, *, explicit=False, inherited=False):
    return ResolvedField(value, source, 1.0, explicit, inherited)


def _context_fields_from_text(text: str) -> dict:
    t=text or ''
    out={}
    if any(x in t for x in ['کافه','cafe','coffee','قهوه']):
        out['scene']='cafe'; out['location']='cafe'
        if any(x in t for x in ['قهوه','coffee','نوشیدن','drinking']): out['activity']='drinking coffee'; out['held_objects']=['coffee cup']
    if any(x in t for x in ['پارک','park']): out['scene']='park'; out['location']='park'
    if any(x in t for x in ['قدم زدن','walking','راه رفتن']): out['activity']='walking'; out['pose']='walking'
    if any(x in t for x in ['سلفی','selfie']): out['camera']='selfie'
    if any(x in t for x in ['آینه','mirror']): out['camera']='mirror_selfie'; out['scene']='mirror'
    if any(x in t for x in ['کت مشکی','black coat']): out['wardrobe']='black coat'
    return out


def merge_image_intent(current_intent: ImageRequestIntent, source_plan: ResolvedImagePlan|None=None, recent_context=None, memory_context=None, routine_context=None) -> dict:
    merged={name:_field(None, Provenance.SYSTEM) for name in PROMPT_RESOLVED_FIELDS}
    def setf(name, value, source, explicit=False, inherited=False):
        if value in (None,'',[],{}): return
        if merged.get(name) is None or merged[name].value in (None,'',[],{}) or explicit:
            merged[name]=ResolvedField(value, source, 1.0, explicit, inherited)
            _log_prompt_field('IMAGE_CONTEXT_FIELD_RESOLVED', user_id=getattr(current_intent, 'user_id', None), field=name, provenance=str(source), action='resolved')
            _log_prompt_field('IMAGE_PROMPT_FIELD_PROVENANCE', user_id=getattr(current_intent, 'user_id', None), field=name, provenance=str(source), action='recorded')
    # 1 explicit current request
    setf('scene', current_intent.scene.scene_key, Provenance.EXPLICIT, True)
    setf('location', current_intent.scene.location or current_intent.scene.scene_key, Provenance.EXPLICIT, True)
    setf('support_surface', current_intent.scene.support_surface, Provenance.EXPLICIT, True)
    setf('pose', current_intent.pose.pose, Provenance.EXPLICIT, True)
    if current_intent.pose.pose == 'walking': setf('activity', 'walking', Provenance.EXPLICIT, True)
    setf('wardrobe', current_intent.wardrobe.wardrobe, Provenance.CORRECTION if current_intent.wardrobe.explicit_current_request else Provenance.EXPLICIT, True)
    setf('camera', current_intent.composition.camera, Provenance.EXPLICIT, True)
    setf('framing', current_intent.composition.framing, Provenance.EXPLICIT, True)
    # lightweight passthrough-to-field extraction for current text/corrections
    for k,v in _context_fields_from_text(json.dumps(current_intent.current_intent if hasattr(current_intent,'current_intent') else current_intent.parse_coverage.passthrough_visual_spans, ensure_ascii=False)).items():
        setf(k, v, Provenance.EXPLICIT, True)
    # 3 recent conversation
    for m in recent_context or []:
        for k,v in _context_fields_from_text(getattr(m, 'content', '') or getattr(m, 'text', '') or str(m)).items(): setf(k, v, Provenance.RECENT)
    # 4 routine/time
    if routine_context:
        if isinstance(routine_context, dict):
            setf('location', routine_context.get('location'), Provenance.ROUTINE)
            setf('time_of_day', routine_context.get('slot_name'), Provenance.ROUTINE)
    # 5 previous valid state
    if source_plan:
        for name in PROMPT_RESOLVED_FIELDS:
            f=getattr(source_plan, name, None)
            if isinstance(f, ResolvedField): setf(name, f.value, Provenance.SOURCE_PLAN, False, True)
    for name, f in merged.items():
        if f.value in (None,'',[],{}): _log_prompt_field('IMAGE_PROMPT_UNRESOLVED_FIELD_OMITTED', field=name, provenance=str(Provenance.SYSTEM), action='omitted')
    return merged

def evaluate_safety_policy(intent: ImageRequestIntent, context: AdultImagePolicyContext|None=None) -> SafetyDecision:
    unsupported=[r for r,v in intent.body_visibility.regions.items() if v.visibility_requested and r in {'genitals'}]
    if intent.content_classification == ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY:
        return SafetyDecision(PolicyDecision.DENY, 'explicit_genital_visibility_not_supported', 'image_policy_unsupported_visibility')
    if unsupported: return SafetyDecision(PolicyDecision.DENY, 'explicit_genital_visibility_not_supported', 'image_policy_unsupported_visibility')
    if intent.content_classification != ContentClassification.NORMAL:
        if context is None: return SafetyDecision(PolicyDecision.DENY, 'adult_policy_context_required', 'image_policy_context_required')
        if not context.adult_enabled: return SafetyDecision(PolicyDecision.DENY, 'adult_generation_globally_disabled', 'image_policy_adult_disabled')
        if not context.adult_addon_owned: return SafetyDecision(PolicyDecision.DENY, 'adult_image_addon_required', 'image_policy_adult_addon_required')
        if not context.adult_addon_enabled: return SafetyDecision(PolicyDecision.DENY, 'adult_image_addon_disabled', 'image_policy_adult_addon_disabled')
        if context.fictional_partner_min_age < 18: return SafetyDecision(PolicyDecision.DENY, 'adult_partner_age_not_eligible', 'image_policy_age_not_eligible')
    return SafetyDecision()

def ensure_visual_profile_v2(db: Session, user: User, profile: PartnerVisualProfile) -> PartnerVisualProfile:
    # Production corrective behavior: never replace established descriptions with generic
    # placeholders during a pipeline upgrade. Version alone is not proof of completeness.
    traits=dict(profile.profile_json or {})
    required=['face_shape','eye_color','hair_color','skin_tone','build']
    source_descriptions=[profile.face_description, profile.hair_description, profile.eye_description, profile.skin_description, profile.body_description, profile.distinguishing_details]
    if profile.base_seed < VENICE_SEED_MIN:
        profile.base_seed = resolve_seed(abs(profile.base_seed or profile.user_id), profile.user_id, 'identity')['identity_seed']
        profile.updated_at=datetime.utcnow(); db.flush()
    complete=all(traits.get(f) for f in required) or all(source_descriptions[:5])
    if complete and (profile.version or 1) < PROFILE_SCHEMA_VERSION:
        traits.setdefault('schema_version', PROFILE_SCHEMA_VERSION)
        traits.setdefault('identity_compatibility_descriptor', identity_descriptor_v2(profile) if 'identity_descriptor_v2' in globals() else {})
        profile.profile_json=traits; profile.version=PROFILE_SCHEMA_VERSION; profile.updated_at=datetime.utcnow(); db.flush()
    return profile

def identity_descriptor_v2(profile: PartnerVisualProfile) -> dict:
    t=profile.profile_json or {}
    def first(*vals):
        return next((v for v in vals if v not in (None,'','unknown','None','null')), None)
    return {
        'partner_name': first(profile.partner_name),
        'fictional_age': profile.fictional_age,
        'gender_presentation': first(profile.gender_presentation),
        'face': first(t.get('face_shape'), profile.face_description),
        'hair': first(t.get('hair_color'), t.get('hair_texture'), profile.hair_description),
        'eyes': first(t.get('eye_color'), t.get('eye_shape'), profile.eye_description),
        'skin': first(t.get('skin_tone'), profile.skin_description),
        'body': first(t.get('build'), t.get('height'), profile.body_description, profile.height_impression),
        'distinguishing_details': first(t.get('feature'), profile.distinguishing_details),
    }

def _compatible_surfaces_for_pose(pose: str|None) -> set[str]:
    return POSE_SUPPORT_COMPATIBILITY.get(str(pose), set().union(*POSE_SUPPORT_COMPATIBILITY.values()))


def _scene_support_for_pose(scene_key: str, pose: str|None, surfaces: list[str]) -> str|None:
    preferred=POSE_SUPPORT_PREFERRED.get(scene_key, {}).get(str(pose))
    compatible=_compatible_surfaces_for_pose(pose)
    if preferred in surfaces and preferred in compatible: return preferred
    return next((s for s in surfaces if s in compatible), None)


def resolve_pose_support(pose, support_surface, scene, pose_provenance=None, support_provenance=None) -> ResolvedPoseSupport:
    pose_field=pose if isinstance(pose, ResolvedField) else ResolvedField(pose, pose_provenance or Provenance.SYSTEM)
    support_field=support_surface if isinstance(support_surface, ResolvedField) else ResolvedField(support_surface, support_provenance or Provenance.SYSTEM)
    compatible=_compatible_surfaces_for_pose(pose_field.value)
    if support_field.value in compatible:
        return ResolvedPoseSupport(pose_field, support_field)
    if not pose_field.explicit_current_request:
        for pose_name, pose_surfaces in POSE_SUPPORT_COMPATIBILITY.items():
            if support_field.value in pose_surfaces:
                return ResolvedPoseSupport(ResolvedField(pose_name, Provenance.COMPATIBILITY_RESOLUTION, explicit_current_request=False, inherited=False), support_field, True, 'pose_support_compatibility_resolution', str(Provenance.COMPATIBILITY_RESOLUTION))
    if pose_field.explicit_current_request and support_field.explicit_current_request:
        return ResolvedPoseSupport(pose_field, support_field, False, str(InvariantCode.EXPLICIT_POSE_SUPPORT_CONFLICT), None)
    env, loc, priv, surfaces, objs, inc = SCENES.get(str(scene), SCENES['living_room'])
    derived=_scene_support_for_pose(str(scene), str(pose_field.value), surfaces) or next(iter(compatible & set(surfaces)), None) or next(iter(compatible), support_field.value)
    provenance=Provenance.COMPATIBILITY_RESOLUTION if pose_field.explicit_current_request else Provenance.POSE_DERIVED
    return ResolvedPoseSupport(pose_field, ResolvedField(derived, provenance, explicit_current_request=False, inherited=False), True, 'pose_support_compatibility_resolution', str(provenance))


def _objects_for_support(support: str) -> list[str]:
    return {'sofa':['sofa'], 'bed':['bed','bedding','pillows'], 'chair':['chair'], 'car_seat':['car seat'], 'floor':[], 'standing':[], 'none':[]}.get(str(support), [])


def _identity_values(profile_desc: dict) -> set[str]:
    vals=set()
    for v in (profile_desc or {}).values():
        if v not in (None,'',[],{}): vals.add(str(v))
    return vals


def _filter_identity_passthrough(details: list[str], desc: dict, raw_request: str) -> list[str]:
    identity_vals=_identity_values(desc)
    out=[]
    for d in details or []:
        sd=sanitize_passthrough_visual_detail(d)
        if not sd: continue
        if any(val and val in sd and val not in (raw_request or '') for val in identity_vals):
            continue
        out.append(sd)
    return list(dict.fromkeys(out))



WARDROBE_TERMS={'کت':'coat','کت شلوار':'suit','پیراهن':'shirt/dress','لباس':'outfit','مانتو':'manteau','دامن':'skirt','کفش':'shoes','شلوار':'pants','black jacket':'black jacket','blue suit':'blue suit','suit':'suit','jacket':'jacket'}
CRITIQUE_PATTERNS=(('under_eye_too_dark',('زیر چشم','زیرچشم','کبود','تیره زیر چشم')),('outfit_not_visible',('کت شلوارش معلوم نیست','لباس معلوم نیست','معلوم نیست','پیدا نیست')),('too_close_up',('خیلی از نزدیک','نزدیکه','کلوزآپ')),('not_similar_enough',('شبیه خودش نیست','شبیه نیست')),('too_artificial',('مصنوعی','غیر طبیعی','غیرطبیعی')),('negative_feedback',('خوب نبود','بد بود','نپسندیدم')),('bad_lighting',('نور بد','تاریک')),('bad_composition',('کادربندی بد','ترکیب بد')))

def extract_visual_critique(text: str) -> list[str]:
    t=(text or '').lower().replace('\u200c',' ')
    out=[]
    for code, pats in CRITIQUE_PATTERNS:
        if any(p in t for p in pats): out.append(code)
    return list(dict.fromkeys(out))

def _wardrobe_from_text(text: str) -> str|None:
    t=(text or '').lower().replace('\u200c',' ')
    hits=[]
    for term, canon in WARDROBE_TERMS.items():
        if term in t: hits.append(canon)
    colors=[]
    for fa,en in [('آبی','blue'),('مشکی','black'),('سیاه','black'),('قرمز','red'),('کرم','cream'),('سفید','white')]:
        if fa in t or en in t: colors.append(en)
    if hits:
        return ' '.join(dict.fromkeys(colors + hits))
    return None

def resolve_visual_requirements(intent: ImageRequestIntent, *, user_request: str='', previous_job=None) -> VisualRequirements:
    action=canonical_image_action(intent.continuity.action)
    text=user_request or ''
    wardrobe=intent.wardrobe.wardrobe or _wardrobe_from_text(text)
    critique=extract_visual_critique(text)
    selfie=(intent.composition.camera == 'selfie') or ('سلفی' in text.lower() or 'selfie' in text.lower())
    explicit_close=bool(intent.composition.framing in {'closeup','portrait'} or selfie)
    vr=VisualRequirements(requested_action=action, style_targets=StyleTargets(wardrobe=wardrobe, expression=','.join(e.value for e in intent.expression_modifiers) or None), correction_signals=critique)
    if wardrobe:
        vr.wardrobe_requested=True; vr.wardrobe_visibility_required=True; vr.visibility_targets.upper_body_visible=True; vr.visibility_targets.full_outfit_visible=any(x in text for x in ['تمام قد','کفش','دامن','شلوار'])
        vr.framing_requirement='full_body' if vr.visibility_targets.full_outfit_visible else 'upper_body_or_three_quarter'
        vr.reason_codes.append('wardrobe_visibility_required')
    elif explicit_close:
        vr.framing_requirement='closeup_allowed'
    else:
        vr.framing_requirement='natural_medium_or_medium_wide'; vr.visibility_targets.upper_body_visible=True
    vr.visibility_targets.hands_visible='دست' in text or 'hand' in text.lower()
    vr.visibility_targets.held_object_visible=bool(intent.scene.spatial_relations or any(x in text for x in ['دستت','لیوان','کتاب','coffee','cup']))
    vr.visibility_targets.environment_visible=bool(intent.scene.scene_key or intent.scene.location or action==ImageAction.NEW_GENERATION)
    vr.continuity_targets.preserve_identity=True
    vr.continuity_targets.preserve_previous_scene=action==ImageAction.REFINEMENT
    vr.continuity_targets.preserve_previous_outfit=action==ImageAction.REFINEMENT
    vr.continuity_targets.deliberately_vary_composition=action in {ImageAction.NEW_GENERATION, ImageAction.VARIATION} and previous_job is not None
    if critique and action==ImageAction.NEW_GENERATION and previous_job is not None:
        vr.requested_action=ImageAction.REFINEMENT
    logger.info('IMAGE_VISUAL_REQUIREMENTS_RESOLVED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', getattr(intent,'user_id',None), getattr(previous_job,'id',None), action, action, vr.reason_codes + critique)
    if vr.wardrobe_visibility_required: logger.info('IMAGE_WARDROBE_VISIBILITY_REQUIRED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', getattr(intent,'user_id',None), getattr(previous_job,'id',None), action, action, ['wardrobe_visibility_required'])
    if critique: logger.info('IMAGE_CRITIQUE_EXTRACTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', getattr(intent,'user_id',None), getattr(previous_job,'id',None), action, action, critique)
    return vr

def plan_continuity(action: str, visual_requirements: VisualRequirements, *, source_job=None) -> ContinuityPlan:
    action=canonical_image_action(action)
    if action==ImageAction.RESEND_EXACT:
        cp=ContinuityPlan(True, True, True, True, [], [])
        logger.info('IMAGE_CONTINUITY_MODE_SELECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', None, getattr(source_job,'id',None), action, action, [])
        return cp
    if action==ImageAction.REFINEMENT:
        cp=ContinuityPlan(True, True, True, True, ['correction'], [])
        logger.info('IMAGE_CONTINUITY_MODE_SELECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', None, getattr(source_job,'id',None), action, action, cp.requested_variation_axes)
        return cp
    if action==ImageAction.VARIATION:
        cp=ContinuityPlan(True, False, False, False, ['pose','camera','framing','scene'], ['exact_crop','same_pose','same_camera'])
        logger.info('IMAGE_CONTINUITY_MODE_SELECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', None, getattr(source_job,'id',None), action, action, cp.requested_variation_axes)
        return cp
    axes=['framing','camera','pose'] + (['scene'] if source_job else [])
    cp=ContinuityPlan(True, False, False, False, axes, ['tight_headshot','passport_centered','same_crop'] if source_job else ['tight_headshot','passport_centered'])
    logger.info('IMAGE_CONTINUITY_MODE_SELECTED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', None, getattr(source_job,'id',None), action, action, cp.requested_variation_axes)
    return cp

def request_fingerprint(text: str, visual_requirements: VisualRequirements) -> str:
    payload={'text': text or '', 'wardrobe': visual_requirements.style_targets.wardrobe, 'corrections': visual_requirements.correction_signals, 'framing': visual_requirements.framing_requirement}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]

def construct_resolved_plan(intent, merged, safety, profile, *, source_job=None, message_id=None, user_request=''):
    scene_field=merged.get('scene', _field(None))
    scene_key=scene_field.value
    surface=merged.get('support_surface', _field(None))
    pose_field=merged.get('pose', _field(None))
    env=loc=priv=None; surfaces=[]; objs=[]; inc=[]
    if scene_key in SCENES:
        env, loc, priv, surfaces, objs, inc = SCENES[scene_key]
    if surface.value and surface.explicit_current_request and not scene_field.explicit_current_request:
        hinted=SUPPORT_SCENE_HINT.get(str(surface.value))
        if hinted and not scene_key:
            scene_key=hinted; scene_field=ResolvedField(scene_key, Provenance.COMPATIBILITY_RESOLUTION)
            env, loc, priv, surfaces, objs, inc = SCENES[scene_key]
    if pose_field.value and surface.value:
        resolved=resolve_pose_support(pose_field, surface, scene_key, pose_field.source, surface.source)
    else:
        resolved=ResolvedPoseSupport(pose_field, surface)
    surface=resolved.support_surface
    if surface.value and scene_key in SCENES and surface.value not in surfaces:
        hinted=SUPPORT_SCENE_HINT.get(str(surface.value))
        if hinted and not scene_field.explicit_current_request:
            scene_key=hinted; scene_field=ResolvedField(scene_key, Provenance.COMPATIBILITY_RESOLUTION); env, loc, priv, surfaces, objs, inc = SCENES[scene_key]
    required=list(dict.fromkeys((list(objs) if scene_key in SCENES else []) + (_objects_for_support(str(surface.value)) if surface.value else [])))
    excluded=[o for o in inc if o not in required and o != surface.value]
    validation={'errors':[], 'warnings':[]}
    if resolved.reason_code == str(InvariantCode.EXPLICIT_POSE_SUPPORT_CONFLICT): validation['errors'].append(resolved.reason_code)
    elif resolved.changed: validation['warnings'].append(resolved.reason_code)
    if not intent.wardrobe.wardrobe:
        inferred=_wardrobe_from_text(user_request)
        if inferred:
            intent.wardrobe.wardrobe=inferred; intent.wardrobe.explicit_current_request=True; merged['wardrobe']=ResolvedField(inferred, Provenance.EXPLICIT, explicit_current_request=True)
    visual_requirements=resolve_visual_requirements(intent, user_request=user_request, previous_job=source_job)
    continuity_plan=plan_continuity(intent.continuity.action, visual_requirements, source_job=source_job)
    fingerprint=request_fingerprint(user_request, visual_requirements)
    variation_index=1 if intent.continuity.action==ImageAction.VARIATION else 0
    src_seed=getattr(source_job,'seed',None)
    seed=resolve_image_seed(profile.base_seed, intent.continuity.action, fingerprint, source_job, continuity_plan.requested_variation_axes)
    ident=identity_descriptor_v2(profile); action=str(canonical_image_action(intent.continuity.action))
    expected_subject_count=2 if intent.secondary_subject.requested or (intent.interaction in {'kiss','hug','holding_hands'} and intent.secondary_subject.role) else 1
    passthrough=_filter_identity_passthrough(list(dict.fromkeys(intent.passthrough_visual_details)), ident, user_request)
    provenance={name:str(getattr(merged.get(name), 'source', Provenance.SYSTEM)) for name in PROMPT_RESOLVED_FIELDS}
    prompt_context=ImagePromptContext(SubjectIdentity(**{**ident, 'identity_fingerprint': hashlib.sha256(json.dumps(ident,sort_keys=True).encode()).hexdigest()}), CurrentVisualRequest({k:asdict(v) for k,v in merged.items() if v.explicit_current_request}, passthrough), ConversationVisualContext({k:asdict(v) for k,v in merged.items() if v.source==Provenance.RECENT}), RoutineVisualContext({k:asdict(v) for k,v in merged.items() if v.source==Provenance.ROUTINE}), VisualContinuityContext({k:asdict(v) for k,v in merged.items() if v.source==Provenance.SOURCE_PLAN}), ResolvedScene({k:asdict(v) for k,v in merged.items()}), ResolvedComposition({'expected_subject_count':expected_subject_count}), ['all subjects fictional adults'], ['exact subject count'])
    return ResolvedImagePlan(action=action, source_image_job_id=getattr(source_job,'id',None), current_intent=asdict(intent), merged_intent={k:asdict(v) for k,v in merged.items()}, scene=ResolvedField(scene_key, scene_field.source, explicit_current_request=scene_field.explicit_current_request, inherited=scene_field.inherited), location=ResolvedField(loc or merged.get('location', _field(None)).value, (merged.get('location') or scene_field).source), environment_type=ResolvedField(env, scene_field.source), privacy=ResolvedField(priv, scene_field.source), support_surface=surface, required_objects=ResolvedField(required, scene_field.source), passthrough_visual_details=passthrough, excluded_objects=ResolvedField(excluded, scene_field.source), activity=merged.get('activity', _field(None)), pose=resolved.pose, wardrobe=merged.get('wardrobe', _field(None)), body_visibility={k:asdict(v) for k,v in intent.body_visibility.regions.items()}, safety_decision=safety, entitlement_decision={'allow':safety.decision==PolicyDecision.ALLOW}, composition={'orientation':'portrait','width':DEFAULT_WIDTH,'height':DEFAULT_HEIGHT,'framing':(visual_requirements.framing_requirement if visual_requirements.framing_requirement else merged.get('framing', _field(None)).value),'wardrobe_requested':visual_requirements.wardrobe_requested,'wardrobe_visibility_required':visual_requirements.wardrobe_visibility_required,'forbidden_repetition_axes':continuity_plan.forbidden_repetition_axes,'requested_variation_axes':continuity_plan.requested_variation_axes,'expected_subject_count':expected_subject_count,'primary_subject_role':'moones_partner','secondary_subject_role':intent.secondary_subject.role,'interaction':intent.interaction,'interaction_requires_consent': bool(intent.interaction in {'kiss','hug','holding_hands'}),'all_subjects_fictional_adults': True,'field_provenance':provenance,'prompt_context':asdict(prompt_context)}, camera=merged.get('camera', _field(None)), lighting=merged.get('lighting', _field(None)), identity={'descriptor':ident,'identity_fingerprint':hashlib.sha256(json.dumps(ident,sort_keys=True).encode()).hexdigest(),'schema_version':PROFILE_SCHEMA_VERSION}, seed_strategy=seed, visual_requirements=visual_requirements, continuity_plan=continuity_plan, request_fingerprint=fingerprint, validation_results=validation)

def validate_plan_invariants(plan: ResolvedImagePlan, *, source_job=None, user_id=None, chat_id=None) -> list[str]:
    errors=[]
    scene_known = plan.scene.value in SCENES
    env, loc, priv, surfaces, objs, inc = SCENES.get(str(plan.scene.value), (None, None, None, [], [], []))
    if scene_known and plan.support_surface.value not in (None, '', [], {}) and plan.support_surface.value not in surfaces: errors.append(InvariantCode.SUPPORT_SCENE_MISMATCH)
    if plan.validation_results.get('errors'):
        errors.extend(plan.validation_results['errors'])
    if plan.support_surface.value not in (None, '', [], {}) and plan.pose.value not in (None, '', [], {}) and plan.support_surface.value not in _compatible_surfaces_for_pose(plan.pose.value): errors.append(InvariantCode.POSE_SUPPORT_MISMATCH)
    if scene_known and any(o not in plan.required_objects.value for o in objs): errors.append(InvariantCode.REQUIRED_OBJECT_MISSING)
    if plan.safety_decision.decision == PolicyDecision.DENY and plan.action not in {ImageAction.DENY, ImageAction.CHAT}: errors.append(InvariantCode.UNSUPPORTED_SAFETY_DOWNGRADE)
    if plan.action == ImageAction.RESEND_EXACT and plan.seed_strategy: errors.append(InvariantCode.RESEND_HAS_GENERATION)
    if plan.action == ImageAction.VARIATION and source_job and plan.seed_strategy.get('final_provider_seed') == source_job.seed: errors.append(InvariantCode.VARIATION_SEED_UNCHANGED)
    if source_job and (source_job.user_id != user_id or source_job.chat_id != chat_id): errors.append(InvariantCode.SOURCE_SCOPE_INVALID)
    bad=re.compile(r'\b(None|null|unknown)\b', re.I)
    if bad.search(json.dumps({k:v for k,v in plan.identity.get('descriptor',{}).items() if v is not None}, ensure_ascii=False)): errors.append(InvariantCode.NULL_IDENTITY_DESCRIPTOR)
    if plan.composition.get('orientation')=='portrait' and plan.composition.get('width',0) > plan.composition.get('height',0): errors.append(InvariantCode.DIMENSION_ORIENTATION)
    plan.validation_results={'errors':[str(e) for e in errors], 'warnings':[]}
    return plan.validation_results['errors']

def _render_field(label: str, field: ResolvedField|None) -> str|None:
    if not isinstance(field, ResolvedField) or field.value in (None,'',[],{}):
        _log_prompt_field('IMAGE_PROMPT_UNRESOLVED_FIELD_OMITTED', field=label, provenance=str(Provenance.SYSTEM), action='omitted')
        return None
    return f"{label}: {field.value}"


def compile_image_prompt(plan: ResolvedImagePlan) -> CompiledImagePrompt:
    desc=plan.identity.get('descriptor',{})
    ident_parts=[f"{k}={v}" for k,v in desc.items() if v not in (None,'',[],{})]
    ident='; '.join(ident_parts)
    content_classification=str(plan.current_intent.get('content_classification') or '').lower()
    allowed_adult_intent=content_classification != 'normal' or bool(plan.body_visibility)
    visibility=', '.join(k for k,v in (plan.body_visibility or {}).items() if v.get('visibility_requested') or v.get('framing_requested'))
    exprs=', '.join(f"{e.get('region') or 'face'} {e.get('value')}" for e in plan.current_intent.get('expression_modifiers', []) if isinstance(e, dict))
    expected_subject_count=int((plan.composition or {}).get('expected_subject_count') or 1)
    interaction=(plan.composition or {}).get('interaction')
    secondary_role=(plan.composition or {}).get('secondary_subject_role')
    sections=[]
    subject_contract = (f"Create a realistic image of exactly {expected_subject_count} fictional adult" + ("s" if expected_subject_count != 1 else "") + " matching the resolved structured plan.")
    if expected_subject_count == 1:
        subject_contract += " Generate exactly one fictional adult person matching the stored subject identity. Do not add another person."
    else:
        subject_contract += " Generate exactly two fictional consenting adults from the resolved identities/roles. Do not add a third person."
    sections.append(subject_contract)
    sections.append(f"Subject identity: {ident}.")
    sections.append("Preserve stable face identity from the visual profile: stored age, gender presentation, face, hair, eyes, skin tone, body build and distinguishing details when specified.")
    vr=getattr(plan, 'visual_requirements', VisualRequirements())
    if vr.wardrobe_visibility_required:
        sections.append("Requested wardrobe must be clearly visible and verifiable. Do not use a tight face-only portrait. Do not use shoulders-only portrait framing. Use an upper-body / three-quarter / full-body composition that shows the clothing color and style.")
    elif vr.framing_requirement == 'natural_medium_or_medium_wide':
        sections.append("Use a natural medium or medium-wide composition, not a passport-style centered tight headshot, unless the request explicitly asks for close-up.")
    elif vr.framing_requirement == 'closeup_allowed':
        sections.append("Close portrait/selfie framing is allowed because the user requested it.")
    if plan.action == ImageAction.NEW_GENERATION:
        sections.append("This is a new image, not an exact repeat; preserve identity while varying pose, camera, crop, and scene enough to avoid a near-duplicate.")
    elif plan.action == ImageAction.VARIATION:
        sections.append("This is a deliberate variation: preserve identity and general concept, but meaningfully change composition, camera angle, pose, and scene details.")
    elif plan.action == ImageAction.REFINEMENT:
        sections.append("This is a refinement: preserve identity and relevant previous image features while applying the requested correction.")
    corrections=[]
    for c in vr.correction_signals:
        corrections.append({'under_eye_too_dark':'Reduce heavy under-eye darkness; keep the face clean, healthy, and naturally lit.','outfit_not_visible':'Make the requested outfit clearly visible.','too_close_up':'Pull the camera back; avoid an overly close crop.','not_similar_enough':'Improve identity consistency with the visual profile.','too_artificial':'Use natural realistic skin texture and lighting.','negative_feedback':'Correct the previous quality issue with a cleaner, more satisfying composition.','bad_lighting':'Improve lighting; avoid muddy shadows.','bad_composition':'Improve composition and framing.'}.get(c,c))
    if corrections: sections.append('Correction constraints from user critique: ' + ' '.join(corrections))
    for label, field in [('Scene', plan.scene), ('Location', plan.location), ('Activity', plan.activity), ('Pose', plan.pose), ('Support surface', plan.support_surface), ('Wardrobe', plan.wardrobe), ('Camera mode', plan.camera), ('Lighting', plan.lighting)]:
        rendered=_render_field(label, field)
        if rendered: sections.append(rendered + '.')
    if plan.required_objects.value:
        sections.append('Visible objects: ' + ', '.join(plan.required_objects.value) + '.')
    if exprs: sections.append('Expression/features: ' + exprs + '.')
    if allowed_adult_intent:
        body_text=('full nudity, ' + visibility if content_classification.endswith('full_nudity') and visibility else (visibility or ('full nudity, full body framing, no genital close-up' if content_classification.endswith('full_nudity') else 'no explicit body emphasis')))
        sections.append('Body visibility: ' + body_text + '.')
    if expected_subject_count == 2:
        interaction_text={'kiss':'mutually kissing with consensual romantic body language','hug':'mutually hugging with consensual affectionate body language','holding_hands':'holding hands with consensual romantic body language'}.get(str(interaction), 'consensual body language')
        sections.append(f"Secondary subject role: one generic fictional adult {secondary_role or 'companion'}, never a real person. Interaction: {interaction_text}.")
    passthrough=[sanitize_passthrough_visual_detail(x) for x in getattr(plan, 'passthrough_visual_details', []) if sanitize_passthrough_visual_detail(x)]
    if passthrough: sections.append('User-requested visual details: ' + '; '.join(passthrough) + '.')
    sections.append('Use a natural, internally consistent composition. Preserve identity consistency. Exactly one person, no duplicate subject, no collage.' if expected_subject_count == 1 else 'Use a natural, internally consistent composition. Preserve identity consistency. No duplicate subject, no collage.')
    positive=' '.join(sections)
    sec={'identity':ident,'visual_requirements':asdict(vr),'continuity_plan':asdict(getattr(plan,'continuity_plan',ContinuityPlan())),'passthrough_visual_details':passthrough,'single_subject_contract':subject_contract,'expected_subject_count':expected_subject_count,'interaction':interaction,'secondary_subject_role':secondary_role,'scene':plan.scene.value,'location':plan.location.value,'activity':plan.activity.value,'pose':plan.pose.value,'support_surface':plan.support_surface.value,'wardrobe':plan.wardrobe.value,'body_visibility':visibility,'expression_modifiers':exprs,'composition':plan.composition,'camera_mode':plan.camera.value,'lighting':plan.lighting.value}
    if expected_subject_count == 2:
        neg_terms=['third person','background person','crowd','group photo','duplicated subject','twins','extra face','extra head','unrelated person','photobomb','reflected extra person','child','teenager','youthful appearance','non-consensual interaction','visible photographer','sexual act beyond requested kiss'] + list(plan.excluded_objects.value or []) + [x for x in plan.current_intent.get('explicit_exclusions', [])]
    else:
        neg_terms=['duplicate person','two people','second person','companion','photographer','camera operator','person in background','background people','extra face','extra head','extra body','reflected person','mirror duplicate','duplicated subject','group photo','couple photo','selfie with another person','photobomb','disembodied hand from another person','cloned face','collage','watermark','malformed hands','bad anatomy'] + list(plan.excluded_objects.value or []) + [x for x in plan.current_intent.get('explicit_exclusions', [])]
        if allowed_adult_intent: neg_terms[2:2]=['twins','split portrait','side-by-side duplicate','multiple subjects','text','logo','identity inconsistency','accidental close-up']
    return CompiledImagePrompt(positive, ', '.join(dict.fromkeys(neg_terms)), {'width':plan.composition['width'],'height':plan.composition['height'],'seed':plan.seed_strategy.get('final_provider_seed')}, sec)

def validate_compiled_prompt(plan: ResolvedImagePlan, compiled: CompiledImagePrompt) -> list[str]:
    errors=[]
    positive=compiled.positive_prompt
    for obj in plan.required_objects.value or []:
        if obj not in positive: errors.append(str(InvariantCode.REQUIRED_OBJECT_MISSING))
        if obj in compiled.negative_prompt: errors.append(str(InvariantCode.PROMPT_CONTRADICTION))
    expected_subject_count=int((plan.composition or {}).get('expected_subject_count') or 1)
    if expected_subject_count == 2:
        if 'exactly 2 fictional adults' not in positive or 'third person' not in compiled.negative_prompt: errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))
    elif 'exactly one fictional adult' not in positive or 'two people' not in compiled.negative_prompt:
        errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))
    # token/field aware rendering: concrete scene/pose/wardrobe/activity requires resolved value and provenance
    for name in ['scene','pose','wardrobe','activity','support_surface']:
        field=getattr(plan, name, None)
        value=getattr(field, 'value', None)
        source=str(getattr(field, 'source', Provenance.SYSTEM))
        rendered=compiled.sections.get(name)
        if rendered not in (None,'',[],{}) and value in (None,'',[],{}):
            _log_prompt_field('IMAGE_PROMPT_INVENTED_FIELD_REJECTED', field=name, provenance=source, action='rejected')
            errors.append(str(InvariantCode.UNSPECIFIED_RENDERED))
        if value not in (None,'',[],{}) and source == str(Provenance.SYSTEM):
            errors.append(str(InvariantCode.INVENTED_FIELD))
    # identity passthrough guard
    identity_vals=_identity_values(plan.identity.get('descriptor',{}))
    current_text=json.dumps(plan.current_intent, ensure_ascii=False)
    for detail in compiled.sections.get('passthrough_visual_details') or []:
        if any(val and val in detail and val not in current_text for val in identity_vals): errors.append(str(InvariantCode.IDENTITY_PASSTHROUGH))
    if str(expected_subject_count) not in positive and ('one' not in positive if expected_subject_count == 1 else 'two' not in positive):
        errors.append(str(InvariantCode.SUBJECT_COUNT_MISMATCH))
    return list(dict.fromkeys(errors))

def plan_to_json(plan: ResolvedImagePlan) -> dict: return asdict(plan)

@dataclass
class ReadOnlyProfileAdapter:
    user_id: int = 0
    version: int = PROFILE_SCHEMA_VERSION
    fictional_age: int = 24
    base_seed: int = 42
    partner_name: str = 'fictional partner'
    gender_presentation: str = 'adult woman'
    face_description: str = 'stable face'
    hair_description: str = 'stable hair'
    eye_description: str = 'stable eyes'
    skin_description: str = 'stable skin'
    body_description: str = 'stable build'
    height_impression: str = 'stable height'
    distinguishing_details: str = 'stable details'
    profile_json: dict = field(default_factory=lambda:{'face_shape':'stable face','eye_color':'stable eyes','hair_color':'stable hair','skin_tone':'stable skin','build':'stable build'})


def route_shadow_decision(text: str, *, source_message_id: int|None=None, legacy_route: str='chat') -> dict:
    norm=normalize_request_v2(text, source_message_id=source_message_id)
    intent=parse_image_intent(norm)
    return {
        'source_message_id': source_message_id,
        'request_hash': hashlib.sha256((text or '').encode()).hexdigest()[:16],
        'legacy_route': legacy_route,
        'v2_detected_action': str(intent.continuity.action),
        'v2_is_image_request': bool(intent.is_image_request),
        'confidence': intent.parse_coverage.confidence,
        'matched_categories': intent.parse_coverage.recognized_categories,
        'fallback_required': intent.parse_coverage.fallback_required,
        'parse_disposition': str(intent.parse_coverage.disposition),
        'passthrough_visual_details': intent.passthrough_visual_details,
    }


def shadow_plan_read_only(text: str, *, user_id: int|None=None, chat_id: int|None=None, source_message_id: int|None=None, legacy_route: str='chat') -> dict:
    norm=normalize_request_v2(text, user_id=user_id, chat_id=chat_id, source_message_id=source_message_id)
    intent=parse_image_intent(norm)
    route_map={'image_explicit':ImageAction.NEW_GENERATION,'image_followup':ImageAction.VARIATION,'image_refinement':ImageAction.REFINEMENT,'image_resend':ImageAction.RESEND_EXACT,'chat':intent.continuity.action}
    if legacy_route in route_map and legacy_route != 'chat':
        intent.continuity.action=route_map[legacy_route]
    policy=evaluate_safety_policy(intent, AdultImagePolicyContext(adult_enabled=False, soft_safety_enabled=True, normal_addon_owned=True, normal_addon_enabled=True, fictional_partner_min_age=24))
    merged=merge_image_intent(intent)
    plan=construct_resolved_plan(intent, merged, policy, ReadOnlyProfileAdapter(user_id=user_id or 0), message_id=source_message_id or 0, user_request=text)
    invariants=validate_plan_invariants(plan, user_id=user_id, chat_id=chat_id)
    compiled=compile_image_prompt(plan)
    prompt_invariants=validate_compiled_prompt(plan, compiled)
    return {
        'request_hash': hashlib.sha256((text or '').encode()).hexdigest()[:16],
        'source_message_id': source_message_id,
        'legacy_route': legacy_route,
        'v2_action': str(intent.continuity.action),
        'fallback_required': intent.parse_coverage.fallback_required,
        'parse_disposition': str(intent.parse_coverage.disposition),
        'passthrough_visual_details': intent.passthrough_visual_details,
        'unmatched_tokens': intent.parse_coverage.unmatched_meaningful_tokens,
        'content_classification': str(intent.content_classification),
        'policy_decision': str(policy.decision),
        'policy_reason': policy.reason_code,
        'scene': plan.scene.value,
        'support_surface': plan.support_surface.value,
        'pose': plan.pose.value,
        'body_regions': list(intent.body_visibility.regions.keys()),
        'expression_modifiers': [asdict(e) for e in intent.expression_modifiers],
        'invariant_codes': [str(e) for e in invariants],
        'prompt_invariant_codes': [str(e) for e in prompt_invariants],
        'identity_fingerprint': plan.identity.get('identity_fingerprint'),
    }
