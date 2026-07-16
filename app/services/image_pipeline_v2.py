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
from app.services.image_semantic_lexicons import IMAGE_SEMANTIC_LEXICONS

PROMPT_ENGINE_VERSION = 'image-prompt-v1.6.11'
PLAN_VERSION = 'resolved-image-plan-v2.0'
PROFILE_SCHEMA_VERSION = 2

class ImageAction(StrEnum):
    NEW_GENERATION='new_generation'; VARIATION='variation'; REFINEMENT='refinement'; RESEND_EXACT='resend_exact'; DENY='deny'; CHAT='chat'
class Provenance(StrEnum):
    EXPLICIT='explicit_current_request'; EXCLUSION='explicit_current_exclusions'; SOURCE_PLAN='source_image_plan'; RECENT='recent_same_chat_message'; MEMORY='recent_visual_memory'; ROUTINE='partner_routine'; PROFILE='profile_default'; SYSTEM='system_default'; COMPATIBILITY_RESOLUTION='compatibility_resolution'; POSE_DERIVED='pose_derived'
class PolicyDecision(StrEnum):
    ALLOW='allow'; DENY='deny'; TRANSFORM='transform'
class InvariantCode(StrEnum):
    EXPLICIT_POSE_SUPPORT_CONFLICT='explicit_pose_support_conflict'; EXPLICIT_OVERWRITTEN='explicit_current_field_overwritten'; SUPPORT_SCENE_MISMATCH='support_surface_scene_mismatch'; POSE_SUPPORT_MISMATCH='pose_support_surface_mismatch'; REQUIRED_OBJECT_MISSING='required_object_missing'; INCOMPATIBLE_OBJECT_PRESENT='incompatible_object_present'; UNSUPPORTED_SAFETY_DOWNGRADE='unsupported_safety_intent_not_downgraded'; RESEND_HAS_GENERATION='resend_has_generation_plan'; VARIATION_SEED_UNCHANGED='variation_seed_unchanged'; SOURCE_SCOPE_INVALID='source_job_scope_invalid'; SOURCE_STALE='source_job_stale'; IDENTITY_INCOMPLETE='identity_profile_incomplete'; NULL_IDENTITY_DESCRIPTOR='identity_descriptor_null_like'; DIMENSION_ORIENTATION='dimension_orientation_mismatch'; PROMPT_CONTRADICTION='prompt_contradiction'; MEANINGFUL_TOKENS_UNMATCHED='meaningful_tokens_unmatched'; ADULT_INTENT_CLASSIFIED_NORMAL='adult_intent_classified_as_normal'; SINGLE_SUBJECT_CONSTRAINT_MISSING='single_subject_constraint_missing'; UNEXPECTED_IDENTITY_FINGERPRINT_CHANGE='unexpected_identity_fingerprint_change'; PROFILE_SCHEMA_INCOMPLETE='profile_schema_version_claims_completeness_missing_fields'; GENERIC_FALLBACK_WITH_UNRESOLVED='generic_fallback_used_despite_meaningful_unresolved_terms'

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
    confidence: float = 1.0
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
class ContinuityIntent: action: str=ImageAction.NEW_GENERATION; source_image_job_id: int|None=None
@dataclass
class IdentityIntent: consistency_level: str='best_effort_text_only'
@dataclass
class VisualAssertion: subject: str; attribute: str; polarity: str; source_span: tuple[int,int]; confidence: float=1.0
@dataclass
class ExpressionModifier: region: str|None; attribute: str; value: str; source_span: tuple[int,int]
@dataclass
class ImageRequestIntent:
    is_image_request: bool=False; route: ImageRouteDecisionV2|None=None; parse_coverage: ParseCoverage=field(default_factory=ParseCoverage); adult_intent: str|None=None; content_classification: str=ContentClassification.NORMAL; body_visibility: BodyVisibilityIntent=field(default_factory=BodyVisibilityIntent); scene: SceneIntent=field(default_factory=SceneIntent); pose: PoseIntent=field(default_factory=PoseIntent); wardrobe: WardrobeIntent=field(default_factory=WardrobeIntent); composition: CompositionIntent=field(default_factory=CompositionIntent); continuity: ContinuityIntent=field(default_factory=ContinuityIntent); identity: IdentityIntent=field(default_factory=IdentityIntent); visual_assertions: list[VisualAssertion]=field(default_factory=list); expression_modifiers: list[ExpressionModifier]=field(default_factory=list); explicit_exclusions: list[str]=field(default_factory=list)
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
    'sofa': {'reclining':'sofa','lying':'sofa','seated':'sofa','standing':'standing','walking':'floor'},
    'bedroom': {'reclining':'bed','lying':'bed','seated':'bed'},
    'bed': {'reclining':'bed','lying':'bed','seated':'bed'},
    'hotel_room': {'reclining':'bed','lying':'bed','seated':'bed'},
    'park': {'reclining':'floor','lying':'floor','seated':'floor','walking':'floor'},
    'beach': {'reclining':'floor','lying':'floor','seated':'floor','walking':'floor'},
    'gym': {'reclining':'floor','lying':'floor','seated':'floor'},
}
SUPPORT_SCENE_HINT={'sofa':'sofa','bed':'bed','car_seat':'car','chair':None,'floor':None,'standing':None,'none':None}

SCENES={
 'bedroom':('home','private bedroom','private',['standing','bed','chair'],['bed','pillows'],[]), 'bed':('home','private bedroom with bed','private',['bed'],['bed','bedding','pillows'],[]), 'living_room':('home','living room','private',['sofa','chair','floor','standing'],['sofa'],['bed']), 'sofa':('home','living room with sofa','private',['sofa','standing','floor'],['sofa','cushions'],['bed']), 'bathroom':('home','bathroom','private',['standing','none'],['mirror','bathroom fixtures'],[]), 'mirror':('home','mirror area','private',['standing','none'],['mirror'],[]), 'hotel_room':('travel','hotel room','private',['bed','chair','standing'],['bed'],[]), 'car':('car','inside a car','private',['car_seat'],['car seat','dashboard'],[]), 'cafe':('cafe','cafe','public',['chair','standing'],['table','chair'],['bed']), 'restaurant':('restaurant','restaurant','public',['chair'],['table','chair'],['bed']), 'street':('outdoor','street','public',['standing'],['street background'],['bed','sofa']), 'park':('outdoor','park','public',['standing','floor'],['trees'],[]), 'beach':('outdoor','beach','public',['standing','floor'],['sand','sea'],[]), 'office':('workplace','office','public',['chair','standing'],['desk','chair'],['bed']), 'university':('campus','university','public',['chair','standing'],['campus background'],['bed']), 'metro':('transit','metro','public',['standing','chair'],['metro car'],['bed']), 'shop':('shop','shop','public',['standing'],['shop shelves'],['bed']), 'gym':('gym','gym','public',['standing','floor'],['gym equipment'],[])}
def _lex_entries(*names):
    out=[]
    for name in names:
        out.extend(IMAGE_SEMANTIC_LEXICONS.get(name, ()))
    return out

def _variants(entry):
    return tuple(entry.persian_variants) + tuple(entry.colloquial_variants)

def _canonical_token(value: str) -> str:
    v=(value or '').replace('‌','').replace('ي','ی').replace('ك','ک')

    # These words genuinely end with «رو».
    # Stripping it turns «خودرو» into «خود» and causes
    # false matches with words such as «خودت».
    if v in {'خودرو', 'مترو'}:
        return v

    suffixes=('مون','تون','شون','ام','ات','اش','مو','تو','شو','رو','را')
    for suf in suffixes:
        if len(v)>len(suf)+1 and v.endswith(suf):
            v=v[:-len(suf)]
            break
    if v in {'مم','ممه'}: return 'ممه'
    if v in {'سین','سين'}: return 'سینه'
    if v == 'کس': return 'کص'
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
    for key in ['activity','camera_framing','wardrobe','adult_intent','body_visibility','exclusions_corrections','expression_modifiers']:
        for m in _semantic_matches(IMAGE_SEMANTIC_LEXICONS[key], tokens, text):
            _record_match(coverage,m)
            if key=='wardrobe': intent.wardrobe=WardrobeIntent(m.canonical, explicit_current_request=True)
            if key=='camera_framing': intent.composition.framing=m.canonical
            if key=='adult_intent': intent.adult_intent=m.canonical; intent.content_classification=ContentClassification.FULL_NUDITY
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
    if intent.adult_intent == 'adult_visual':
        for region in (
            'breasts',
            'buttocks',
            'full_body',
        ):
            intent.body_visibility.regions.setdefault(
                region,
                BodyRegionIntent(
                    mentioned=True,
                    visibility_requested=True,
                    visibility_negated=False,
                    framing_requested=False,
                    explicit_current_request=True,
                    source_spans=[],
                ),
            )

        intent.content_classification = (
            ContentClassification.FULL_NUDITY
        )

    elif any(
        region == 'genitals'
        and visibility.visibility_requested
        for region, visibility
        in intent.body_visibility.regions.items()
    ):
        intent.content_classification = (
            ContentClassification
            .UNSUPPORTED_EXPLICIT_VISIBILITY
        )

    elif (
        intent.wardrobe.wardrobe
        == 'lingerie'
    ):
        intent.content_classification = (
            ContentClassification.LINGERIE
        )

    elif any(
        region in {
            'breasts',
            'buttocks',
        }
        and visibility.visibility_requested
        for region, visibility
        in intent.body_visibility.regions.items()
    ):
        intent.content_classification = (
            ContentClassification.SUGGESTIVE
        )

    else:
        intent.content_classification = (
            ContentClassification.NORMAL
        )
    stop={'عکس','بده','بفرست','یه','یک','من','تو','باش','باشه','باشی','بشه','توش','رو','را','و','از','با','این','بار','قبلی','دیگه','مثل','داده','درد','دار','توضیح','پزشکی','شماره','کشیده','بزن'}
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
    coverage.fallback_required=bool(freq and intent.is_image_request)
    coverage.confidence=0.7 if coverage.fallback_required else 1.0
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
    defaults={'scene':'living_room','support_surface':'chair','pose':'seated','wardrobe':'context-appropriate clothing','lighting':'natural soft light','camera':'candid smartphone photo'}
    for k,v in defaults.items(): setf(k,v,Provenance.SYSTEM)
    return merged

def evaluate_safety_policy(intent: ImageRequestIntent, context: AdultImagePolicyContext|None=None) -> SafetyDecision:
    unsupported=[r for r,v in intent.body_visibility.regions.items() if v.visibility_requested and r in {'genitals'}]
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
    d={
        'partner_name': first(profile.partner_name, 'fictional partner'),
        'fictional_age': profile.fictional_age,
        'gender_presentation': first(profile.gender_presentation, 'adult feminine presentation'),
        'face': first(t.get('face_shape'), profile.face_description),
        'hair': first(t.get('hair_color'), t.get('hair_texture'), profile.hair_description),
        'eyes': first(t.get('eye_color'), t.get('eye_shape'), profile.eye_description),
        'skin': first(t.get('skin_tone'), profile.skin_description),
        'body': first(t.get('build'), t.get('height'), profile.body_description, profile.height_impression),
        'distinguishing_details': first(t.get('feature'), profile.distinguishing_details),
    }
    for k in list(d):
        if d[k] is None:
            d[k]=hashlib.sha256(f'{profile.user_id}:{profile.base_seed}:{k}'.encode()).hexdigest()[:8]
    return d

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


def construct_resolved_plan(intent, merged, safety, profile, *, source_job=None, message_id=None, user_request=''):
    scene_key=merged['scene'].value
    surface=merged['support_surface']

    if (
        merged['pose'].explicit_current_request
        and not merged['scene'].explicit_current_request
    ):
        current_scene = SCENES.get(
            scene_key,
            SCENES['living_room'],
        )
        current_surfaces = current_scene[3]
        compatible_surfaces = (
            _compatible_surfaces_for_pose(
                merged['pose'].value
            )
        )

        if not (
            set(current_surfaces)
            & compatible_surfaces
        ):
            scene_key = 'living_room'

            if not surface.explicit_current_request:
                surface = ResolvedField(
                    (
                        'standing'
                        if merged['pose'].value == 'standing'
                        else SCENES['living_room'][3][0]
                    ),
                    Provenance.COMPATIBILITY_RESOLUTION,
                    explicit_current_request=False,
                    inherited=False,
                )

    if surface.explicit_current_request and not merged['scene'].explicit_current_request:
        hinted=SUPPORT_SCENE_HINT.get(str(surface.value))
        if hinted: scene_key=hinted
    env, loc, priv, surfaces, objs, inc = SCENES.get(scene_key, SCENES['living_room'])
    if scene_key in SCENES and surface.value not in surfaces and not surface.explicit_current_request:
        surface=ResolvedField(surfaces[0], Provenance.SYSTEM)
    if (
        intent.composition.framing == 'full_body'
        and not merged['pose'].explicit_current_request
        and not surface.explicit_current_request
        and 'standing' in surfaces
    ):
        merged['pose'] = ResolvedField(
            'standing',
            Provenance.SYSTEM,
            explicit_current_request=False,
        )

        surface = ResolvedField(
            'standing',
            Provenance.SYSTEM,
            explicit_current_request=False,
        )

    resolved=resolve_pose_support(
        merged['pose'],
        surface,
        scene_key,
        merged['pose'].source,
        surface.source,
    )
    surface=resolved.support_surface

    scene_explicit_in_user_text = bool(
        intent.scene.source_spans
    )
    pose_explicit_current = bool(
        merged['pose'].explicit_current_request
    )
    support_explicit_current = bool(
        merged['support_surface'].explicit_current_request
    )

    if surface.value not in surfaces:
        if (
            pose_explicit_current
            and not support_explicit_current
        ):
            scene_key = 'living_room'
        else:
            hinted = SUPPORT_SCENE_HINT.get(
                str(surface.value)
            )

            if (
                hinted
                and not scene_explicit_in_user_text
            ):
                scene_key = hinted
            elif not scene_explicit_in_user_text:
                scene_key = 'living_room'

        (
            env,
            loc,
            priv,
            surfaces,
            objs,
            inc,
        ) = SCENES.get(
            scene_key,
            SCENES['living_room'],
        )

        if (
            surface.value not in surfaces
            and not support_explicit_current
        ):
            compatible_surface = (
                _scene_support_for_pose(
                    scene_key,
                    resolved.pose.value,
                    surfaces,
                )
            )

            if compatible_surface:
                surface = ResolvedField(
                    compatible_surface,
                    Provenance.COMPATIBILITY_RESOLUTION,
                    explicit_current_request=False,
                    inherited=False,
                )
    required=list(dict.fromkeys(list(objs) + _objects_for_support(str(surface.value))))
    excluded=[o for o in inc if o not in required and o != surface.value]
    validation={'errors':[], 'warnings':[]}
    if resolved.reason_code == str(InvariantCode.EXPLICIT_POSE_SUPPORT_CONFLICT): validation['errors'].append(resolved.reason_code)
    elif resolved.changed: validation['warnings'].append(resolved.reason_code)
    variation_index=1 if intent.continuity.action==ImageAction.VARIATION else 0
    src_seed=getattr(source_job,'seed',None)
    seed=resolve_seed(profile.base_seed, message_id or 0, user_request, variation_index=variation_index, source_seed=src_seed)
    ident=identity_descriptor_v2(profile); action=str(intent.continuity.action)
    return ResolvedImagePlan(action=action, source_image_job_id=getattr(source_job,'id',None), current_intent=asdict(intent), merged_intent={k:asdict(v) for k,v in merged.items()}, scene=ResolvedField(scene_key, merged['scene'].source, explicit_current_request=merged['scene'].explicit_current_request, inherited=merged['scene'].inherited), location=ResolvedField(loc, Provenance.SYSTEM), environment_type=ResolvedField(env, Provenance.SYSTEM), privacy=ResolvedField(priv, Provenance.SYSTEM), support_surface=surface, required_objects=ResolvedField(required), excluded_objects=ResolvedField(excluded), pose=resolved.pose, wardrobe=merged['wardrobe'], body_visibility={k:asdict(v) for k,v in intent.body_visibility.regions.items()}, safety_decision=safety, entitlement_decision={'allow':safety.decision==PolicyDecision.ALLOW}, composition={'orientation':'portrait','width':DEFAULT_WIDTH,'height':DEFAULT_HEIGHT,'framing':intent.composition.framing or 'environmental three-quarter'}, camera=merged['camera'], lighting=merged['lighting'], identity={'descriptor':ident,'identity_fingerprint':hashlib.sha256(json.dumps(ident,sort_keys=True).encode()).hexdigest(),'schema_version':PROFILE_SCHEMA_VERSION}, seed_strategy=seed, validation_results=validation)

def validate_plan_invariants(plan: ResolvedImagePlan, *, source_job=None, user_id=None, chat_id=None) -> list[str]:
    errors=[]
    env, loc, priv, surfaces, objs, inc = SCENES.get(str(plan.scene.value), SCENES['bedroom'])
    if plan.support_surface.value not in surfaces: errors.append(InvariantCode.SUPPORT_SCENE_MISMATCH)
    if plan.validation_results.get('errors'):
        errors.extend(plan.validation_results['errors'])
    if plan.support_surface.value not in _compatible_surfaces_for_pose(plan.pose.value): errors.append(InvariantCode.POSE_SUPPORT_MISMATCH)
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
    desc=plan.identity.get('descriptor',{})
    ident=', '.join(str(v) for v in desc.values() if v)
    visibility=', '.join(k for k,v in (plan.body_visibility or {}).items() if v.get('visibility_requested') or v.get('framing_requested')) or 'no explicit body emphasis'
    exprs=', '.join(f"{e.get('region') or 'face'} {e.get('value')}" for e in plan.current_intent.get('expression_modifiers', []) if isinstance(e, dict))
    single=(
        'exactly one fictional adult person; '
        'one real adult woman only; '
        'one continuous single-panel photograph; '
        'one uninterrupted camera frame; '
        'exactly one face, one head, one torso, '
        'two arms and two legs; '
        'the same single person from head to toe; '
        'no second person, no duplicated subject, '
        'no split-screen, no side-by-side layout, '
        'no collage, no diptych, '
        'no before-and-after composition'
    )
    scene=f"in {plan.location.value} with {', '.join(plan.required_objects.value or [])}"

    framing = str(
        plan.composition.get('framing')
        or 'environmental three-quarter'
    )

    framing_instructions = {
        'full_body': (
            'one continuous single-panel full-length '
            'head-to-toe photograph; '
            'one centered standing woman only; '
            'the entire single body must be visible '
            'from the top of the head through both feet; '
            'both feet fully inside the same frame; '
            'leave visible space above the head and '
            'below the feet; no body cropping; '
            'no split frame and no second panel'
        ),
        'portrait': (
            'portrait composition focused on the '
            'head and upper torso'
        ),
        'selfie': (
            'natural arm-length smartphone selfie '
            'composition'
        ),
        'closeup': (
            'close-up composition focused on the face'
        ),
        'environmental three-quarter': (
            'environmental three-quarter composition'
        ),
    }

    framing_instruction = (
        framing_instructions.get(
            framing,
            framing,
        )
    )
    wardrobe=str(plan.wardrobe.value)

    content_classification = str(
        plan.current_intent.get(
            'content_classification'
        )
        or ContentClassification.NORMAL
    )

    if (
        content_classification
        == str(
            ContentClassification.FULL_NUDITY
        )
    ):
        wardrobe = (
            'no clothing; fully nude adult woman; '
            'no dress, no shirt, no pants, '
            'no underwear, no lingerie, '
            'no bra, no fabric covering the body'
        )

    elif (
        content_classification
        == str(
            ContentClassification.TOPLESS
        )
    ):
        wardrobe = (
            'topless adult woman; bare chest; '
            'no shirt and no bra; '
            'lower body remains covered'
        )

    elif (
        content_classification
        == str(
            ContentClassification.LINGERIE
        )
    ):
        wardrobe = (
            'adult lingerie only; '
            'lingerie clearly visible; '
            'no outerwear and no casual clothing'
        )

    elif (
        content_classification
        == str(
            ContentClassification.SUGGESTIVE
        )
    ):
        wardrobe = (
            'revealing adult outfit appropriate '
            'for a private scene'
        )

    elif wardrobe in {
        '',
        'None',
        'context-appropriate clothing',
    }:
        wardrobe = (
            'tasteful casual clothing '
            'appropriate for the scene'
        )
    ident = ident.replace(
        'دختر',
        'adult woman',
    )

    ident = ident.replace(
        'adult female',
        'adult woman',
    )

    if (
        content_classification
        == str(ContentClassification.NORMAL)
    ):
        ident = ident.replace(
            'adult body proportions',
            'natural body proportions',
        )

        ident = ident.replace(
            'adult woman',
            'woman',
        )

    else:
        ident = ident.replace(
            'adult body proportions',
            'natural adult body proportions',
        )

    age_value = desc.get(
        'fictional_age'
    )

    try:
        apparent_age = int(age_value)
    except (TypeError, ValueError):
        apparent_age = None

    if (
        apparent_age is not None
        and 18 <= apparent_age <= 34
    ):
        age_instruction = (
            f'visibly about {apparent_age} years old; '
            f'a youthful adult face appropriate for '
            f'age {apparent_age}; '
            'smooth natural adult skin; '
            'fresh youthful facial proportions; '
            'no deep wrinkles, no aged skin, '
            'no gray hair, not middle-aged, '
            'not elderly'
        )

    elif (
        apparent_age is not None
        and apparent_age <= 45
    ):
        age_instruction = (
            f'visibly about {apparent_age} years old; '
            'natural age-appropriate adult appearance; '
            'not elderly'
        )

    else:
        age_instruction = (
            'clearly adult appearance with '
            'natural age-appropriate features'
        )

    sections={'identity':ident,'single_subject_contract':single,'scene':scene,'pose':f"{plan.pose.value} on {plan.support_surface.value}",'wardrobe':wardrobe,'body_visibility':visibility,'expression_modifiers':exprs,'composition':plan.composition,'lighting':str(plan.lighting.value)}
    positive=(
        f"Create a realistic candid smartphone image of "
        f"{single}. The subject is {ident}. "
        f"Age appearance: {age_instruction}. "
        f"Show the same single woman {scene}, "
        f"{sections['pose']}. "
        f"Framing: {framing_instruction}. "
        f"Wardrobe: {wardrobe}. "
        f"Body visibility: {visibility}. "
        f"Expression/features: "
        f"{exprs or 'natural expression'}. "
        f"Use {sections['lighting']} and preserve "
        f"identity consistency."
    )

    scene_intent = (
        plan.current_intent.get('scene')
        or {}
    )

    spatial_relations = (
        scene_intent.get('spatial_relations')
        or []
    )

    semantic_objects = []

    for relation in spatial_relations:
        if not isinstance(relation, dict):
            continue

        obj = relation.get('object')

        if obj:
            semantic_objects.append(str(obj))

    visual_assertions = (
        plan.current_intent.get(
            'visual_assertions'
        )
        or []
    )

    freeform_constraints = []

    for assertion in visual_assertions:
        if not isinstance(assertion, dict):
            continue

        if (
            assertion.get('subject')
            == 'freeform_visual_constraints'
        ):
            value = assertion.get('polarity')

            if value:
                freeform_constraints.append(
                    str(value)
                )

    semantic_objects = list(
        dict.fromkeys(semantic_objects)
    )

    freeform_constraints = list(
        dict.fromkeys(freeform_constraints)
    )

    if semantic_objects:
        positive += (
            " Clearly include these requested visual "
            "objects: "
            + ", ".join(semantic_objects)
            + "."
        )

    if freeform_constraints:
        positive += (
            " Additional visual requirements: "
            + "; ".join(freeform_constraints)
            + "."
        )
    neg_terms=['duplicate person','two people','twins','cloned face','split portrait','side-by-side duplicate','collage','diptych','multiple subjects','text','watermark','logo','bad anatomy','malformed hands','identity inconsistency','accidental close-up'] + list(plan.excluded_objects.value or []) + [x for x in plan.current_intent.get('explicit_exclusions', [])]

    neg_terms.extend([
        'split-screen',
        'split screen',
        'two-panel image',
        'two panel layout',
        'multi-panel image',
        'multiple frames',
        'divided canvas',
        'before and after layout',
        'two separate photographs',
        'duplicated woman',
        'duplicated body',
        'duplicated face',
        'two faces',
        'two heads',
        'two bodies',
        'extra person',
        'mirror clone',
        'elderly woman',
        'old woman',
        'middle-aged appearance',
        'aged face',
        'aged skin',
        'deep wrinkles',
        'gray hair',
    ])

    if framing == 'full_body':
        neg_terms.extend([
            'cropped body',
            'cropped feet',
            'feet outside frame',
            'cut off legs',
            'cut off head',
            'waist-up portrait',
            'half-body portrait',
            'close-up framing',
            'zoomed-in composition',
        ])
    return CompiledImagePrompt(positive, ', '.join(dict.fromkeys(neg_terms)), {'width':plan.composition['width'],'height':plan.composition['height'],'seed':plan.seed_strategy.get('final_provider_seed')}, sections)

def validate_compiled_prompt(plan: ResolvedImagePlan, compiled: CompiledImagePrompt) -> list[str]:
    errors=[]
    for obj in plan.required_objects.value or []:
        if obj not in compiled.positive_prompt: errors.append(str(InvariantCode.REQUIRED_OBJECT_MISSING))
    for obj in plan.required_objects.value or []:
        if obj in compiled.negative_prompt: errors.append(str(InvariantCode.PROMPT_CONTRADICTION))
    if 'exactly one fictional adult person' not in compiled.positive_prompt or 'two people' not in compiled.negative_prompt: errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))
    return errors

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
