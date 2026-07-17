from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
import hashlib, json, logging, re
from sqlalchemy import select, inspect
from sqlalchemy.orm import Session
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationFeedback
from app.models.user import User
from app.services.addon_service import ADULT_IMAGE_GENERATION_UNLOCK, user_owns_addon, user_addon_enabled

IMAGE_ADDON_KEY = 'image_generation_unlock'
logger = logging.getLogger(__name__)

PROMPT_ENGINE_VERSION = 'image-prompt-v1.6.0'

ANTI_TEXT_POSITIVE_CONSTRAINT = (
    'Hard visual constraints: no readable text, no Persian text, no Arabic text, '
    'no wall writing, no posters with writing, no signs with writing, no captions, '
    'no watermark, no logo, no typography, no subtitles, no decorative readable calligraphy.'
)
ANTI_TEXT_NEGATIVE_TERMS = (
    'text, watermark, logo, caption, poster, signage, Persian writing, Arabic writing, '
    'wall text, typography, readable letters, readable words, subtitles, calligraphy'
)
VISUAL_DEFECT_NEGATIVE_TERMS = 'ugly, unattractive, uncanny face, distorted face, asymmetrical eyes, crossed eyes, malformed eyes, waxy skin, plastic skin, over-smoothed skin, excessive makeup, deformed hands, malformed hands, fused fingers, missing fingers, extra fingers, duplicate limbs, disconnected limbs, twisted arms, broken anatomy, impossible pose, floating body, warped furniture, distorted sofa, bad perspective, awkward crop, cropped body, cropped furniture, stiff pose, overly posed, oversaturated, harsh flash, low-detail face, inconsistent identity, duplicate person'
ENVIRONMENTAL_NEGATIVE_TERMS = 'close-up portrait, tight crop, face filling frame, headshot, shoulders-only portrait, centered beauty portrait, direct-to-camera beauty shot, medium-close portrait, face-dominant composition, passport photo, generic selfie close-up, tight close-up, close-up headshot, face-only crop, shoulders-up framing, passport-style crop, studio portrait'
NORMAL_NEGATIVE_PROMPT = f'blurry, lowres, deformed, bad hands, bad anatomy, cartoon, anime, {VISUAL_DEFECT_NEGATIVE_TERMS}, {ANTI_TEXT_NEGATIVE_TERMS}'
ADULT_NEGATIVE_PROMPT = f'blurry, lowres, deformed, censored, bad hands, bad anatomy, cartoon, anime, {VISUAL_DEFECT_NEGATIVE_TERMS}, {ANTI_TEXT_NEGATIVE_TERMS}'
HARD_BLOCK = ['زیر ۱۸','زیر18','نوجوان','بچه','کودک','اجبار','زور','تجاوز','بی رضایت','بی‌رضایت','محارم','حیوان','deepfake','دیپ فیک','minor','underage','coercion','non-consent','incest','bestiality','real person']
ADULT_WORDS = []  # legacy name; use adult_requested() normalized detector



@dataclass
class NormalizedRequest:
    raw: str
    normalized: str
    tokens: list[str]
    offsets: list[tuple[int, int]]

@dataclass
class ImageRouteDecision:
    route: str = 'chat'
    explicit_image_request: bool = False
    contextual_followup: bool = False
    recent_image_context_found: bool = False
    source_image_job_id: int | None = None
    confidence: float = 0.0
    reason_code: str = 'no_image_intent'

@dataclass
class SafetyDecision:
    decision: str = 'allow'
    reason_code: str | None = None

@dataclass
class ImageRequestIntent:
    is_image_request: bool = False
    adult_intent: str = 'none'
    nudity_level: str = 'none'
    wardrobe_intent: str = 'tasteful casual clothing suited to the scene'
    body_emphasis: list[str] = field(default_factory=list)
    scene: str | None = None
    environment_type: str | None = None
    location: str | None = None
    activity: str | None = None
    pose: str | None = None
    support_surface: str | None = None
    held_objects: list[str] = field(default_factory=list)
    camera_mode: str | None = None
    shot_type: str | None = None
    orientation: str | None = None
    subject_frame_share: str | None = None
    lighting: str | None = None
    daypart: str | None = None
    mood: str | None = None
    continuity_action: str = 'unspecified'
    explicit_exclusions: list[str] = field(default_factory=list)
    explicit_current_fields: list[str] = field(default_factory=list)
    field_provenance: dict[str, str] = field(default_factory=dict)
    field_confidence: dict[str, float] = field(default_factory=dict)
    safety_signals: list[str] = field(default_factory=list)
    explicit_body_visibility: list[str] = field(default_factory=list)

@dataclass
class ResolvedImagePlan:
    intent: ImageRequestIntent
    safety_decision: SafetyDecision
    visual_scene_state: VisualSceneState
    composition_plan: CompositionPlan
    wardrobe_plan: str
    positive_constraints: list[str]
    negative_constraints: list[str]
    prompt: str
    negative_prompt: str
    width: int
    height: int
    orientation: str
    validation_results: list[str]
    prompt_engine_version: str = PROMPT_ENGINE_VERSION
    privacy_policy_result: str = 'allow'

class ImagePromptInvariantError(RuntimeError):
    def __init__(self, codes: list[str]):
        self.codes = codes
        super().__init__('image_prompt_invariant_failed:' + ','.join(codes))

@dataclass
class ImagePromptResult:
    prompt: str
    negative_prompt: str
    content_mode: str
    scene_type: str
    location: str
    camera: str
    lighting: str
    pose: str
    wardrobe: str
    continuity_notes: str
    prompt_engine_version: str = PROMPT_ENGINE_VERSION
    safety_decision: str = 'allow'
    safety_reason: str | None = None
    influenced_by_job_ids: list[int] = field(default_factory=list)
    input_context_summary: str = ''
    width: int = 1024
    height: int = 1280
    orientation: str = 'portrait'
    adult_visual_intent: str = 'none'
    adult_intent_explicit_current_request: bool = False
    stale_scene_reset: bool = False
    stale_scene_reset_reason: str | None = None
    final_environment_type: str | None = None
    final_wardrobe_intent: str | None = None
    adult_nudity_level: str = 'none'
    adult_body_emphasis: list[str] = field(default_factory=list)
    adult_scene_override: list[str] = field(default_factory=list)
    adult_pose_override: list[str] = field(default_factory=list)
    final_pose_type: str | None = None
    resolved_plan: ResolvedImagePlan | None = None

@dataclass
class ExtractedImageContext:
    scene_context: str | None = None
    pose_context: str | None = None
    mood_context: str | None = None
    time_context: str | None = None
    explicit_visual_constraints: list[str] = field(default_factory=list)
    refinement_after_critique: bool = False



_DIGIT_TRANS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')

def normalize_request(text: str) -> NormalizedRequest:
    raw = text or ''
    t = raw.lower().translate(_DIGIT_TRANS)
    t = t.replace('\u200c', ' ').replace('ي', 'ی').replace('ك', 'ک')
    t = t.replace('ۀ', 'ه').replace('ة', 'ه').replace('ؤ', 'و')
    t = re.sub(r'[ـًٌٍَُِّْ]', '', t)
    t = re.sub(r'[^\w\sآ-ی-]', ' ', t)
    t = re.sub(r'می\s+شه', 'میشه', t)
    t = re.sub(r'می\s+خوام', 'میخوام', t)
    t = re.sub(r'\s+', ' ', t).strip()
    tokens=[]; offsets=[]
    for m in re.finditer(r'\S+', t):
        tok=m.group(0)
        # Split common colloquial possessive suffixes for matching while preserving offsets.
        split=False
        for suf in ('هام','هات','هاش'):
            if tok.endswith(suf) and len(tok)>len(suf)+1:
                tokens.extend([tok[:-len(suf)], suf]); offsets.extend([m.span(), m.span()]); split=True; break
        if not split:
            tokens.append(tok); offsets.append(m.span())
    return NormalizedRequest(raw=raw, normalized=t, tokens=tokens, offsets=offsets)

def normalize_persian_text(text: str) -> str:
    return normalize_request(text).normalized

@dataclass
class CompositionPlan:
    composition_key: str
    shot_type: str
    camera_angle: str
    subject_scale: str
    orientation: str
    environment_visibility: str
    pose_constraints: str
    requested_close_framing: bool = False
    subject_frame_share: str = '25%–45%'
    camera_distance: str = 'camera positioned a few steps away'
    required_environment_objects: list[str] = field(default_factory=list)
    width: int = 1024
    height: int = 1280

@dataclass
class VisualSceneState:
    scene: str | None = None
    environment_type: str | None = None
    location: str | None = None
    subject_action: str | None = None
    held_objects: list[str] = field(default_factory=list)
    support_surface: str | None = None
    pose: str | None = None
    activity: str | None = None
    mood: str | None = None
    daypart: str | None = None
    clothing: str | None = None
    weather: str | None = None
    camera_request: str | None = None
    shot_type: str | None = None
    source_message: str | None = None
    visual_corrections: list[str] = field(default_factory=list)
    source_role: str | None = None
    source_message_id: int | None = None
    source_created_at: datetime | None = None
    fallback_fields: list[str] = field(default_factory=list)

_LOCATION_PATTERNS = [
    (r'خیابون|خیابان|پیاده رو|شهر', ('outdoor_street', 'Tehran street')), (r'کوچه', ('outdoor_street', 'quiet alley in Tehran')),
    (r'پارک', ('park', 'urban park in Tehran')), (r'کافه|کافی شاپ', ('cafe', 'cafe in Tehran')),
    (r'رستوران', ('restaurant', 'restaurant in Tehran')), (r'ماشین|توی خودرو|تو خودرو', ('car', 'inside a car')),
    (r'مترو', ('metro', 'Tehran metro')), (r'فروشگاه|مغازه|مال|مرکز خرید', ('shop', 'shop or shopping center')),
    (r'محل کار|سر کار|دفتر|اداره', ('workplace', 'workplace/office')), (r'دانشگاه|دانشکده', ('university', 'university campus')),
    (r'باشگاه', ('gym', 'gym')), (r'ساحل|کنار دریا', ('beach', 'beach')), (r'سفر|هتل', ('travel', 'travel setting')),
    (r'روی مبل|رو مبل|مبل|کاناپه|خونه|خانه|اتاق|پذیرایی|نشیمن', ('home', 'private home interior')),
    (r'روی تخت|رو تخت|تو تخت|توی تخت|تو رخت خواب|توی رخت خواب|رخت خواب|تخت|زیر پتو', ('home', 'bedroom/home setting with a clearly visible bed')),
]
_ACTIVITY_PATTERNS = [
    (r'بستنی.*می ?خور|می ?خور.*بستنی|آیس ?کریم|ice cream', ('eating ice cream', 'eating and holding ice cream', ['ice cream'])),
    (r'قهوه.*(?:می ?خور|خورد|بنوش|نوش)|کافه.*(?:می ?خور|خورد)|می ?نوش.*قهوه|coffee|drinking coffee', ('drinking coffee', 'drinking coffee and naturally interacting with a coffee cup', ['coffee cup'])),
    (r'قدم می ?زن|راه می ?رم|پیاده روی', ('walking', 'walking naturally', [])), (r'خرید', ('shopping', 'shopping', ['shopping bag'])),
    (r'رانندگی|دارم می ?رونم|پشت فرمون', ('driving', 'driving', [])), (r'آشپزی|غذا درست', ('cooking', 'cooking', [])),
    (r'کار می ?کنم|مشغول کار', ('working', 'working', [])), (r'کتاب می ?خون|مطالعه', ('reading', 'reading', ['book'])),
    (r'ورزش|تمرین', ('exercising', 'exercising', [])), (r'نشستم|نشسته', ('sitting', 'sitting naturally', [])),
    (r'لم دادم|لم داده|لم داده روی تخت|دراز کشید|دراز کشیده|ولو شدم|تکیه دادم', ('reclining', 'reclining comfortably', [])),
    (r'دستمه|گرفتم|نگه داشتم|holding', ('holding an object', 'holding an object visibly', [])),
]
_FIELD_PATTERNS = {
    'pose': [(r'لم دادم رو مبل|لم دادم|لم داده|لمم|ولو شدم|دراز کشیدم|دراز کشیده|تکیه دادم|تکیه داده|روی کاناپه ام|رو کاناپه ام|جمع شدم روی مبل|زیر پتو ام|رفتم تو تخت', 'reclining comfortably / lying back naturally with body supported by furniture'), (r'نشستم|نشسته', 'seated naturally'), (r'ایستادم|ایستاده', 'standing naturally'), (r'راه می ?رم|قدم می ?زن', 'walking naturally')],
    'mood': [(r'خوابم میاد|خواب آلودم|خواب الودم|برای خواب|قبل خواب|می خوام بخوابم', 'sleepy, relaxed, winding down before sleep'), (r'آروم|اروم|ریلکس|راحت', 'relaxed and intimate'), (r'زشته|خوب نشده|خوب نشد', 'needs more attractive natural quality')],
    'daypart': [(r'نیمه شب|دیر وقت', 'late night'), (r'شب|قبل خواب|خواب', 'night'), (r'صبح', 'morning'), (r'عصر|غروب', 'evening')],
    'clothing': [(r'لباس راحتی|لباس خونه|پیژامه|پتو', 'tasteful comfortable home clothing suited to winding down'), (r'مانتو|پالتو|کاپشن', 'tasteful streetwear suited to the weather')],
    'camera_request': [(r'سلفی|selfie', 'selfie'), (r'تمام قد|قدی|فول بادی|full body', 'full body'), (r'پرتره|portrait|کلوز ?آپ|close[ -]?up|هدشات|headshot|face shot|عکس صورت', 'portrait')],
}
_CORRECTIONS = [(r'لم ندادی|لم نداده|دراز نکشیدی', 'force reclining pose; exclude sitting upright, standing, formal portrait'), (r'مبل.*معلوم نیست|مبل هم معلوم نیست|کاناپه معلوم نیست', 'sofa must be clearly visible; use environmental framing'), (r'نوشته داره|متن داره|نوشته', 'strengthen no-text constraints; plain walls without readable writing'), (r'زشته|خوب نشده|خوب نشد|بهتر بده', 'improve flattering believable lighting, facial harmony, natural expression, anatomy, and composition'), (r'شبیه خودت نیست|شبیه نیست', 'reinforce established facial identity and visual profile'), (r'مصنوعیه|مصنوعی', 'natural skin texture, candid pose, realistic lighting; no plastic skin')]

def _field_value(field: str, text: str) -> str | None:
    nt = normalize_persian_text(text)
    return next((v for pat, v in _FIELD_PATTERNS.get(field, []) if re.search(pat, nt)), None)

SCENE_ONTOLOGY = {
    'bed': {'environment_type':'home','location':'private bedroom with a clearly visible bed','scene':'private bedroom with a clearly visible bed','privacy':'private','support_surface':'bed','objects':['bed','bedding','pillows'],'phrases':['روی تخت','رو تخت','تو تخت','توی تخت','تو رخت خواب','توی رخت خواب','رخت خواب','تختخواب','تخت']},
    'bedroom': {'environment_type':'home','location':'private bedroom','scene':'private bedroom interior','privacy':'private','support_surface':None,'objects':[],'phrases':['اتاق خواب','bedroom']},
    'sofa': {'environment_type':'home','location':'private home living room with sofa','scene':'home interior with a clearly visible sofa','privacy':'private','support_surface':'sofa','objects':['sofa','cushions'],'phrases':['روی مبل','رو مبل','مبل','کاناپه','روی کاناپه']},
    'mirror': {'environment_type':'home','location':'private mirror area','scene':'mirror photo setup with visible mirror','privacy':'private','support_surface':None,'objects':['mirror'],'phrases':['جلوی آینه','جلو آینه','آینه','mirror']},
    'bathroom': {'environment_type':'home','location':'private bathroom','scene':'private bathroom interior','privacy':'private','support_surface':None,'objects':['bathroom fixtures'],'phrases':['حمام','دوش','bathroom','shower']},
    'hotel': {'environment_type':'travel','location':'private hotel room','scene':'hotel room interior','privacy':'private','support_surface':None,'objects':[],'phrases':['هتل','اتاق هتل','hotel room']},
    'cafe': {'environment_type':'cafe','location':'cafe in Tehran','scene':'cozy cafe interior','privacy':'public','support_surface':'chair','objects':['table','coffee cup','chair'],'phrases':['کافی شاپ','کافه','cafe']},
    'restaurant': {'environment_type':'restaurant','location':'restaurant in Tehran','scene':'restaurant interior','privacy':'public','support_surface':'chair','objects':['table','chair'],'phrases':['رستوران','restaurant']},
    'car': {'environment_type':'car','location':'inside a car','scene':'inside a car interior','privacy':'private','support_surface':'car_seat','objects':['car seat'],'phrases':['داخل ماشین','توی ماشین','تو ماشین','ماشین','خودرو','car']},
    'park': {'environment_type':'park','location':'urban park in Tehran','scene':'urban park','privacy':'public','support_surface':None,'objects':['park greenery'],'phrases':['پارک','park']},
    'street': {'environment_type':'outdoor_street','location':'Tehran street','scene':'outdoor Tehran street context','privacy':'public','support_surface':None,'objects':['street context'],'phrases':['خیابون','خیابان','پیاده رو','کوچه','شهر']},
    'metro': {'environment_type':'metro','location':'Tehran metro','scene':'metro setting','privacy':'public','support_surface':None,'objects':['metro interior'],'phrases':['مترو']},
    'shop': {'environment_type':'shop','location':'shop or shopping center','scene':'shop interior','privacy':'public','support_surface':None,'objects':['shop displays'],'phrases':['فروشگاه','مغازه','مرکز خرید','مال']},
    'workplace': {'environment_type':'workplace','location':'workplace/office','scene':'workplace office','privacy':'public','support_surface':'chair','objects':['desk'],'phrases':['محل کار','سر کار','دفتر','اداره']},
    'university': {'environment_type':'university','location':'university campus','scene':'university campus','privacy':'public','support_surface':None,'objects':['campus context'],'phrases':['دانشگاه','دانشکده']},
    'gym': {'environment_type':'gym','location':'gym','scene':'gym interior','privacy':'public','support_surface':None,'objects':['gym equipment'],'phrases':['باشگاه','gym']},
    'beach': {'environment_type':'beach','location':'beach','scene':'beach / seaside','privacy':'public','support_surface':None,'objects':['shoreline'],'phrases':['کنار دریا','ساحل','beach']},
    'home': {'environment_type':'home','location':'private home interior','scene':'private Iranian home interior','privacy':'private','support_surface':None,'objects':[],'phrases':['خونه','خانه','منزل','اتاق','پذیرایی','نشیمن']},
}

def _negated_near(tokens:list[str], idx:int, window:int=3) -> bool:
    neg={'نه','نذار','نباشه','نباشن','نشه','نشن','نشود','نیست','معلوم نباشن','معلوم نباشه'}
    lo=max(0, idx-window); hi=min(len(tokens), idx+window+1)
    joined=' '.join(tokens[lo:hi])
    return any(n in tokens[lo:hi] for n in neg) or bool(re.search(r'(معلوم|مشخص|دیده|پیدا) ن(?:شه|شن|باشه|باشن)', joined))

def _scene_from_text(text: str) -> dict:
    nr=normalize_request(text); nt=nr.normalized; out={}; candidates=[]
    for key, spec in SCENE_ONTOLOGY.items():
        for phrase in spec['phrases']:
            nphrase=normalize_persian_text(phrase)
            m=re.search(r'(?<!\w)'+re.escape(nphrase)+r'(?!\w)', nt)
            if m:
                # more words/chars = more specific; earlier explicit spans win secondarily
                candidates.append((len(nphrase.split()), len(nphrase), -m.start(), key, spec, m.span(), nphrase))
    if candidates:
        candidates.sort(reverse=True)
        _,_,_,key,spec,span,phrase=candidates[0]
        out.update(environment_type=spec['environment_type'], location=spec['location'], scene=spec['scene'], support_surface=spec.get('support_surface'))
        out['matched_scene_key']=key; out['matched_scene_span']=span; out['matched_scene_phrase']=phrase
        if key=='mirror': out['camera_request']='mirror_photo'
    for pat,(act,action,objects) in _ACTIVITY_PATTERNS:
        if re.search(pat, nt): out.update(activity=act, subject_action=action, held_objects=objects); break
    if 'کتاب' in nt and 'book' not in out.get('held_objects',[]): out.update(activity=out.get('activity') or 'reading', subject_action='holding or reading a book', held_objects=list(dict.fromkeys(out.get('held_objects',[])+['book'])))
    for f in ['pose','mood','daypart','clothing','camera_request']:
        v=_field_value(f, text)
        if v and not out.get(f): out[f]=v
    if re.search(r'نه درازکش|درازکش نباش|دراز نکش', nt):
        out['explicit_exclusions']=['reclining'];
        if out.get('pose') and 'reclining' in out['pose']: out.pop('pose',None)
    if re.search(r'تخت معلوم نباش|تخت.*نباش', nt): out.setdefault('explicit_exclusions',[]).append('bed')
    return out

def extract_refinement_constraints(text: str) -> list[str]:
    nt = normalize_persian_text(text)
    return [v for pat, v in _CORRECTIONS if re.search(pat, nt)]

def _message_parts(m):
    return (getattr(m,'role', m.get('role') if isinstance(m,dict) else None), getattr(m,'content', m.get('content','') if isinstance(m,dict) else ''), getattr(m,'id', m.get('id') if isinstance(m,dict) else None), getattr(m,'created_at', m.get('created_at') if isinstance(m,dict) else None))

def resolve_visual_scene_state(user_request: str, recent_conversation=None, stored_state: dict | None = None) -> VisualSceneState:
    current={'role':'user','content':user_request or '','id':None,'created_at':datetime.utcnow()}
    state=VisualSceneState(); explicit=_scene_from_text(user_request or '')
    sources=[]
    if explicit: sources.append(('explicit_current_request', current, explicit))
    for m in reversed(list(recent_conversation or [])):
        role,content,mid,created=_message_parts(m); vals=_scene_from_text(content)
        if vals and role in ('assistant', None) and (vals.get('scene') or vals.get('activity')): sources.append(('latest_partner_self_description', m, vals)); break
    for m in reversed(list(recent_conversation or [])):
        role,content,mid,created=_message_parts(m); vals=_scene_from_text(content)
        if vals and role=='user' and (vals.get('scene') or vals.get('activity')): sources.append(('recent_user_context', m, vals)); break
    if stored_state: sources.append(('recent_visual_scene_memory', {'role':'memory','content':stored_state.get('source_message') or '','id':stored_state.get('source_message_id'),'created_at':None}, stored_state))
    for source_name,m,vals in sources:
        for k,v in vals.items():
            if hasattr(state,k) and (getattr(state,k) in (None, []) or source_name=='explicit_current_request'):
                setattr(state,k,v)
        role,content,mid,created=_message_parts(m); state.source_role=source_name; state.source_message_id=mid; state.source_created_at=created; state.source_message=content
        if source_name in ('explicit_current_request','latest_partner_self_description') and (state.scene or state.activity): break
    # Fill non-conflicting descriptive fields from recent context (e.g. separate "on sofa" and "sleepy" messages) without overriding the chosen scene.
    for f in ['mood','daypart','clothing','camera_request']:
        if not getattr(state, f):
            for m in reversed(list(recent_conversation or [])):
                _, content, _, _ = _message_parts(m)
                v = _field_value(f, content)
                if v:
                    setattr(state, f, v); break
    corrections=[]
    for m in list(recent_conversation or [])+[current]:
        role,content,_,_=_message_parts(m)
        if role in (None,'user'): corrections.extend(extract_refinement_constraints(content))
    state.visual_corrections=list(dict.fromkeys(corrections))
    if state.visual_corrections: state.visual_corrections.insert(0,'avoid the previous mismatch; do not use upright portrait framing')
    if any('reclining' in c for c in state.visual_corrections): state.pose='reclining comfortably / lying back naturally with body supported by furniture'
    if any('sofa' in c for c in state.visual_corrections): state.scene='home interior with a clearly visible sofa'; state.environment_type='home'
    return state

SCENE_BASED_ENVIRONMENTS = {'cafe','outdoor_street','home','park','car','restaurant','shop','metro','workplace','university','gym','beach','travel'}
SCENE_BASED_ACTIVITIES = {'drinking coffee','eating ice cream','walking','sitting','shopping','driving','cooking','working','reading'}
PUBLIC_ENVIRONMENTS = {'cafe','outdoor_street','park','restaurant','shop','metro','workplace','university'}

def _explicit_scene_continuity_requested(text: str) -> bool:
    nt = normalize_persian_text(text or '')
    return bool(re.search(r'همونجا|همینجا|تو همون کافه|همون اتاق|همون حالت|همون تو کافه', nt))

def _explicit_close_framing_requested_by_user(text: str) -> bool:
    nt = normalize_persian_text(text or '')
    return bool(re.search(r'سلفی|selfie|کلوز ?آپ|close[ -]?up|پرتره|portrait|face shot|عکس صورت', nt))

def _is_scene_based_request(state: VisualSceneState) -> bool:
    return bool(state.environment_type in SCENE_BASED_ENVIRONMENTS or state.scene or state.activity in SCENE_BASED_ACTIVITIES)

def plan_composition(state: VisualSceneState, recent_jobs: list[ImageGenerationJob] | None = None) -> CompositionPlan:
    pose_scene=' '.join([state.pose or '', state.scene or '', state.environment_type or '', state.activity or '']).lower()
    outdoor=state.environment_type in {'outdoor_street','park','beach','travel','university'}
    scene_based=_is_scene_based_request(state)
    recent_keys=[]
    for j in recent_jobs or []:
        meta=j.metadata_json or {}; recent_keys.append(meta.get('composition_key') or (meta.get('composition') or {}).get('composition_key'))
    if state.camera_request=='selfie':
        return CompositionPlan('selfie','natural selfie','handheld phone angle','head-and-shoulders to half body allowed because selfie was explicitly requested','portrait','natural background visible but secondary','natural selfie framing, not overly posed',True,'close framing allowed','handheld phone distance',[],1024,1280)
    if state.camera_request=='portrait':
        return CompositionPlan('portrait','requested portrait photo','natural phone angle','portrait or close framing allowed because explicitly requested','portrait','simple natural background visible but secondary','portrait framing requested by user',True,'close framing allowed','portrait camera distance',[],1024,1280)
    if any(x in pose_scene for x in ['reclining','lying','sofa','bed']):
        return CompositionPlan('seated candid' if 'seated' in pose_scene else 'three-quarter','environmental medium-wide candid','natural smartphone perspective','visible torso and body posture, subject occupies about 25%–45% of the frame and must not fill most of the frame','landscape','sofa/bed and surrounding home environment clearly visible','body positioned along visible supporting furniture, believable weight/contact with cushions, not sitting upright, not standing, no cropped furniture',False,'25%–45%','camera positioned a few steps away',['visible supporting sofa or bed','visible cushions or bedding'],1280,1024)
    if outdoor or state.activity in {'eating ice cream','walking','shopping'}:
        required = ['readable street context'] if state.environment_type == 'outdoor_street' else ['readable surrounding environment']
        if state.activity == 'eating ice cream': required.append('visible ice cream')
        return CompositionPlan('environmental wide','wide environmental candid','camera positioned a few steps away at natural eye level','visible torso/body posture, subject occupies about 25%–45% of the frame, subject must not fill most of the frame','landscape','environment clearly visible and readable around the subject','natural action pose with visible object interaction when relevant; avoid direct eye contact unless requested; avoid face-only or shoulders-up framing',False,'25%–45%','camera positioned a few steps away',required,1280,1024)
    if state.camera_request=='full body' or 'standing' in pose_scene:
        return CompositionPlan('full-body','full-body or three-quarter candid','natural eye-level phone angle','full body or three-quarter body','portrait','environment visible enough for context','natural standing posture',False,'25%–45%','camera positioned a few steps away',[],1024,1280)
    if scene_based:
        return CompositionPlan('environmental medium-wide','medium-wide environmental candid','camera positioned a few steps away at natural eye level','visible torso and body posture, subject occupies about 25%–45% of the frame, subject must not fill most of the frame','landscape','scene environment visibly readable from the framing','activity-specific natural pose; avoid direct eye contact unless requested; avoid face-only or shoulders-up framing',False,'25%–45%','camera positioned a few steps away',[],1280,1024)
    return CompositionPlan('environmental candid','medium-wide environmental candid daily-life photo','camera positioned a few steps away at natural eye level','visible torso and body posture, subject occupies about 25%–45% of the frame, subject must not fill most of the frame','portrait','believable environment visible and readable','relaxed natural pose; avoid direct eye contact unless requested; avoid face-only or shoulders-up framing unless explicitly requested',False,'25%–45%','camera positioned a few steps away',[],1024,1280)

def validate_plan_invariants(plan) -> list[str]:
    if plan is None:
        return []
    codes=[]
    comp=plan.composition_plan; state=plan.visual_scene_state; intent=plan.intent
    if comp.orientation == 'portrait' and comp.width > comp.height: codes.append('dimensions_orientation_mismatch')
    if comp.orientation == 'landscape' and comp.width < comp.height: codes.append('dimensions_orientation_mismatch')
    if intent.adult_intent == 'topless' and (intent.nudity_level != 'topless' or 'breasts_visible' not in intent.body_emphasis): codes.append('topless_intent_mismatch')
    if state.support_surface == 'sofa' and any('bed' in o for o in comp.required_environment_objects): codes.append('bed_object_in_sofa_scene')
    return codes

def _has_positive_closeup(text: str) -> bool:
    """Detect requested close framing after removing explicitly negative clauses."""
    p=(text or '').lower()
    p=re.sub(r'\b(?:no|avoid|without|exclude|not)\b[^,;.]*(?:close[ -]?up|headshot)[^,;.]*', '', p)
    return bool(re.search(r'(?:close[ -]?up|headshot)', p))

def validate_prompt_invariants(plan, prompt: str, negative_prompt: str) -> list[str]:
    p=(prompt or '').lower(); n=(negative_prompt or '').lower(); codes=[]
    if '45%–70%' in p and '25%–45%' in p: codes.append('conflicting_frame_share')
    if 'mandatory correction: resolve prompt contradictions' in p: codes.append('raw_correction_text')
    positive_closeup = _has_positive_closeup(p)
    if 'full-body' in p and positive_closeup: codes.append('full_body_closeup_contradiction')
    if 'standing' in p and 'reclining' in p and 'not standing' not in p: codes.append('standing_reclining_contradiction')
    for term in ['topless','lingerie','full nudity']:
        if term in p and term in n and 'conversion' not in n: codes.append('positive_negative_overlap_'+term.replace(' ','_'))
    return list(dict.fromkeys(codes))

def validate_prompt_contradictions(prompt: str, state: VisualSceneState, composition: CompositionPlan) -> list[str]:
    p=prompt.lower(); issues=[]
    if state.environment_type in {'outdoor_street','park','beach','travel'} and any(x in p for x in ['home interior','sofa','bedroom','indoor lighting']): issues.append('outdoor_home_contradiction')
    if state.activity=='eating ice cream' and not ('ice cream' in p and any(x in p for x in ['holding','eating','licking'])): issues.append('missing_ice_cream_interaction')
    positive_closeup = _has_positive_closeup(p)
    if state.camera_request=='full body' and positive_closeup: issues.append('full_body_closeup_contradiction')
    if state.pose and 'reclining' in state.pose and not any(x in p for x in ['sofa','bed','cushion','furniture']): issues.append('reclining_without_support')
    return issues

def is_explicit_image_request(text: str) -> bool:
    t = re.sub(r'\s+', ' ', text or '').strip().lower()
    if not t:
        return False
    # Avoid broad matches for discussion/metadata about photos; require an
    # imperative/request verb near photo/image/selfie terms.
    media = r'(?:عکس|تصویر|سلفی|عکست|عکس\s*خودتو|تصویر\s+از\s+خودت)'
    request = r'(?:بفرست|بفرس|بفرستی|بده|بدی|بساز|بسازی|درست\s+کن|درست\s+کنی|ارسال\s+کن|نشونم\s+بده)'
    polite = r'(?:یه|یک|لطفاً|لطفا|میشه|می\s*شه|برام|از\s+خودت|خودتو|خودت|رو|را|هم)?'
    patterns = [
        rf'{polite}\s*{media}\s*(?:از\s+خودت|خودتو|خودت|برام|رو|را)?\s*{request}',
        rf'{request}\s*(?:یه|یک)?\s*{media}',
        r'عکس\s+(?:توی|تو|در)\s+',
        r'عکس\s+الان',
        r'(?:قدی|تمام\s*قد|فول\s*بادی)\s*(?:بفرست|بفرس|بده)',
        r'(?:یه|یک)\s+(?:عکس|دونه)\s+(?:دیگه\s+)?(?:بفرست|بفرس|بده)',
        r'(?:بفرست|بفرس)\s+عکس(?:تو|ت\s*رو)?',
    ]
    return any(re.search(p, t) for p in patterns)

def decide_image_route(text: str, *, recent_image_job_id: int | None = None, recent_image_context_found: bool = False) -> ImageRouteDecision:
    nt=normalize_persian_text(text or '')
    explicit=is_explicit_image_request(nt)
    deictic=bool(re.search(r'بده عکسشو|عکسش رو بده|همونو (?:بده|دوباره بفرست)|همون عکس رو بده|اونو دوباره بفرست|عکس قبلی رو بفرست', nt))
    follow=bool(re.search(r'یکی دیگه بگیر|یه دونه دیگه(?: بفرست)?|دوباره (?:عکس بده|بفرست)|همونجوری یکی دیگه|مثل قبلی|این بار|یه بهترش بده|آره بگیر|حالا یکی دیگه', nt))
    non_image=bool(re.search(r'یکی دیگه بگو|دوباره توضیح بده|مثل قبلی جواب بده', nt))
    if (deictic or follow) and recent_image_context_found and not non_image:
        if re.search(r'عکس قبلی رو بفرست|اونو دوباره بفرست|همونو (?:بده|دوباره بفرست)|همون عکس رو بده', nt): route='image_resend'
        else: route='image_refinement' if re.search(r'ولی|این بار|مثل قبلی', nt) else 'image_followup'
        return ImageRouteDecision(route, False, True, True, recent_image_job_id, .9, 'deictic_image_followup' if deictic else 'contextual_image_followup')
    if explicit:
        route='image_refinement' if 'مثل قبلی' in nt or 'این بار' in nt else 'image_explicit'
        return ImageRouteDecision(route, True, False, recent_image_context_found, recent_image_job_id, .95, 'explicit_image_request')
    return ImageRouteDecision('chat', False, False, recent_image_context_found, recent_image_job_id, .6, 'chat_or_no_recent_image_context')

def _adult_norm(text: str) -> str:
    t = normalize_persian_text(text)
    t = re.sub(r'[^\w\sآ-ی]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

@dataclass
class AdultVisualIntent:
    is_adult: bool = False
    intent_type: str = 'none'
    nudity_level: str = 'none'
    body_emphasis: list[str] = field(default_factory=list)
    scene_override: list[str] = field(default_factory=list)
    pose_override: list[str] = field(default_factory=list)
    explicit_current_request: bool = False
    requested_clothing_state: str | None = None
    requires_private_setting: bool = False
    requested_body_framing: str | None = None
    explicit_body_visibility: list[str] = field(default_factory=list)
    denial_reason: str | None = None

    @property
    def has_explicit_adult_scene_terms(self) -> bool:
        return bool(self.scene_override or self.pose_override)

ADULT_INTENT_PATTERNS = [
    r'\b(?:لخت|برهنه|بدون لباس|بی لباس|عریان|نود|نودز|nude|nudes|پورن|porn|سکسی|جنسی|شهوانی|تاپلس|لباس زیر)\b',
    r'\b(?:ممه|ممه ها|سینه|سینه ها|پستان|پستون|کون|باسن|کس|واژن|آلت|کیر)\b',
    r'\b(?:سکس|سکسی|جق|خودارضایی|ارضا|سکسچت|لاپایی)\b',
]

def _has_topless_visibility(nr: NormalizedRequest) -> bool:
    toks=nr.tokens
    body={'ممه','سینه','سینت','پستان','پستون'}
    vis={'معلوم','مشخص','پیدا','نمایان','دیده','واضح'}
    for i,tok in enumerate(toks):
        if tok in body or tok.startswith(('ممه','سینه','پستان','پستون')):
            if _negated_near(toks, i, 4):
                continue
            lo=max(0,i-8); hi=min(len(toks),i+9)
            win=toks[lo:hi]; joined=' '.join(win)
            if any(v in win for v in vis) or re.search(r'تو(ی)? عکس باش', joined):
                return True
    return False

def _has_genital_visibility(nr: NormalizedRequest) -> bool:
    toks=nr.tokens; body={'کس','واژن','فرج','آلت','کیر','اندام','تناسلی','شرمگاه'}; vis={'معلوم','مشخص','پیدا','نمایان','دیده','واضح','باز'}
    for i,tok in enumerate(toks):
        joined=' '.join(toks[max(0,i-2):min(len(toks),i+3)])
        is_body = tok in body or tok.startswith(('واژن','تناسلی','شرمگاه')) or 'اندام تناسلی' in joined
        if is_body and not _negated_near(toks, i, 4):
            win=toks[max(0,i-8):min(len(toks),i+9)]
            if any(v in win for v in vis) or re.search(r'تو(ی)? عکس باش|نشون بده|قابل دید', ' '.join(win)):
                return True
    return False

def _negative_nonvisual_body_context(nr: NormalizedRequest) -> bool:
    nt=nr.normalized
    return bool(re.search(r'درد (?:سینه|واژن)|(?:سینه|واژن|آلت|اندام تناسلی) چیست|راجع به (?:ممه|سینه|واژن|آلت|اندام تناسلی) حرف بزن|لباس روی سینه', nt))

def resolve_adult_visual_intent(user_request: str) -> AdultVisualIntent:
    nr=normalize_request(user_request); t=nr.normalized
    if not t or _negative_nonvisual_body_context(nr):
        return AdultVisualIntent()
    genital_visibility = _has_genital_visibility(nr)
    sexual_activity = bool(re.search(r'\b(?:سکس|جق|خودارضایی|ارضا|سکسچت|لاپایی|sex|sexual)\b', t))
    semi_nude = bool(re.search(r'\b(?:نیمه لخت|نیمه برهنه|semi nude|half nude)\b', t))
    full_nudity = bool(re.search(r'\b(?:کاملا لخت|کامل لخت|برهنه|بدون لباس|بی لباس|عریان|نود|نودز|nude|nudes)\b', t) or (re.search(r'\bلخت\b', t) and not re.search(r'بالا تن|بالا تنه', t))) and not semi_nude
    topless = bool(re.search(r'\b(?:تاپلس|بالا تنت لخت|بالا تنه لخت|بدون سوتین|بی سوتین|topless)\b', t)) or _has_topless_visibility(nr)
    lingerie = bool(re.search(r'\b(?:لباس زیر|لنجری|lingerie|underwear)\b', t))
    suggestive = bool(re.search(r'\b(?:لباس جذاب|سکسی|شهوانی|یقه باز|کلیویج|ناز|تحریک کننده)\b', t))
    body_mentioned = bool(re.search(r'\b(?:ممه|سینه|سینت|پستان|پستون|کون|باسن)\b', t))
    scene = _scene_from_text(t)
    scene_override=[]
    if scene.get('support_surface')=='bed': scene_override.append('bed')
    elif scene.get('scene') and 'bedroom' in scene.get('scene',''): scene_override.append('bedroom')
    elif scene.get('environment_type')=='home': scene_override.append('private_home')
    pose_override=[]
    if scene.get('support_surface')=='bed': pose_override.append('on_bed')
    if re.search(r'\b(?:دراز کشیده|درازکش|lying|lie down)\b', t): pose_override.append('lying')
    if re.search(r'\b(?:لم داده|لم بدی|reclining)\b', t): pose_override.append('reclining')
    body_emphasis=[]
    if topless or body_mentioned:
        body_emphasis += ['breasts_visible','upper_body']
    if genital_visibility:
        body_emphasis += ['genitals_visible']
    if re.search(r'تمام قد|قدی|فول بادی|full body', t): body_emphasis.append('full_body')
    body_emphasis=list(dict.fromkeys(body_emphasis))
    explicit_visibility=[]
    if topless: explicit_visibility.append('breasts')
    if re.search(r'\b(?:کون|باسن)\b', t): explicit_visibility.append('buttocks')
    if genital_visibility: explicit_visibility.append('genitals')
    if genital_visibility:
        return AdultVisualIntent(True, 'unsupported_explicit_genital_visibility', 'explicit_genitals', body_emphasis, scene_override, list(dict.fromkeys(pose_override)), True, None, True, None, explicit_visibility, 'explicit_genital_visibility_not_supported')
    if sexual_activity: typ='sexual_activity'
    elif full_nudity: typ='full_nudity'
    elif topless or semi_nude: typ='topless'
    elif lingerie: typ='lingerie'
    elif suggestive or body_mentioned: typ='suggestive'
    else: return AdultVisualIntent()
    clothing={
        'full_nudity':'fully nude fictional consenting adult, no clothing and no underwear',
        'topless':'fictional consenting adult topless: upper body uncovered, breasts visible, no top, no shirt, no bra; preserve lower-body clothing unless separately requested',
        'lingerie':'wearing the specifically requested adult lingerie; lingerie remains visible',
        'suggestive':'suggestive adult styling requested by the user without extra nudity',
        'sexual_activity':'fictional consenting adult sexual activity requested by the user',
    }[typ]
    nudity={'full_nudity':'full_nudity','topless':'topless','lingerie':'lingerie','suggestive':'suggestive','sexual_activity':'sexual_activity'}[typ]
    if typ=='full_nudity' and not body_emphasis: body_emphasis=['full_body']
    return AdultVisualIntent(True, typ, nudity, body_emphasis, scene_override, list(dict.fromkeys(pose_override)), True, clothing, typ in {'full_nudity','topless','lingerie','sexual_activity'}, ('three-quarter candid composition; subject occupies about 45%–70% of the frame; upper torso and requested body area clearly visible; no body-part-only close-up; not overly distant' if typ in {'full_nudity','topless'} else None), explicit_visibility, None)

def adult_requested(text: str) -> bool:
    return resolve_adult_visual_intent(text).is_adult

def _parse_minimum_age(raw: str | None) -> tuple[int | None, bool]:
    t=normalize_persian_text(str(raw or '')).translate(_DIGIT_TRANS)
    nums=[int(x) for x in re.findall(r'\d+', t)]
    if not nums: return None, False
    if re.search(r'بالای\s*21|بزرگتر از\s*21|21\s*\+', t): return 22, True
    if re.search(r'زیر\s*21|کمتر از\s*21|حدود|تقریبا|تقریباً', t): return min(nums), False
    if len(nums)>=2: return min(nums[0], nums[1]), min(nums[0], nums[1]) >= 21
    return nums[0], nums[0] >= 21

def _age_from_user(user: User) -> int:
    minimum, eligible = _parse_minimum_age(getattr(user, 'partner_age_range', None))
    return minimum or 0

TRAIT_BANK = {
    'face_shape':['soft square face','heart-shaped face','long oval face','round face with defined cheekbones','diamond face'],
    'jaw':['soft tapered jaw','defined straight jaw','rounded jawline','angular but natural jaw'],
    'skin_tone':['light olive skin','warm beige skin','medium olive skin','honey tan skin','fair warm skin'],
    'eye_shape':['almond eyes','slightly hooded eyes','large expressive eyes','deep-set eyes'],
    'eye_color':['dark brown eyes','hazel brown eyes','warm amber-brown eyes','deep black-brown eyes'],
    'eyebrow_shape':['straight full eyebrows','soft arched eyebrows','thick groomed eyebrows','naturally tapered eyebrows'],
    'nose':['straight medium nose','soft rounded nose','slender bridge nose','prominent natural Persian nose'],
    'hair_texture':['silky straight hair','soft wavy hair','thick wavy hair','curly textured hair'],
    'hair_color':['dark chestnut hair','black-brown hair','deep brown hair','auburn-brown hair'],
    'hair_style':['shoulder-length styled hair','long layered styled hair','neat short groomed hair','medium textured side-part hair'],
    'feature':['small beauty mark near cheek','subtle dimple when smiling','distinct cupid bow lips','gentle under-eye warmth'],
    'build':['slim natural build','average healthy build','soft athletic build','curvy natural build','lean athletic build'],
    'height':['slightly tall impression','average height impression','petite-to-average height impression','tall graceful impression'],
    'beard':['clean-shaven','short boxed beard','neat stubble','trimmed mustache and stubble'],
}

def _pick(seed:int, key:str):
    vals=TRAIT_BANK[key]; return vals[int(hashlib.sha256(f'{seed}:{key}'.encode()).hexdigest()[:8],16)%len(vals)]

def ensure_visual_profile(db: Session, user: User) -> PartnerVisualProfile:
    existing = db.scalar(select(PartnerVisualProfile).where(PartnerVisualProfile.user_id == user.id))
    if existing:
        try:
            from app.services.image_pipeline_v2 import ensure_visual_profile_v2
            return ensure_visual_profile_v2(db, user, existing)
        except Exception:
            return existing
    seed = int(hashlib.sha256(f'{user.id}:{user.partner_name}:{user.partner_gender}'.encode()).hexdigest()[:8], 16) % 2147483647
    gender=(user.partner_gender or 'feminine').lower()
    presentation = 'masculine' if gender in {'male','man','masculine','مرد'} else ('neutral' if gender in {'neutral','nonbinary','non-binary'} else 'feminine')
    traits={k:_pick(seed,k) for k in TRAIT_BANK}
    grooming = {'feminine':'tasteful natural makeup, styled well-kept hair, polished but believable appearance', 'masculine':f'groomed hair, {traits["beard"]}, polished but believable appearance', 'neutral':'polished gender-neutral presentation with neat hair and believable styling'}[presentation]
    face=f'{traits["face_shape"]}, {traits["jaw"]}, {traits["eyebrow_shape"]}, {traits["nose"]}, {traits["feature"]}'
    hair=f'{traits["hair_color"]}, {traits["hair_texture"]}, {traits["hair_style"]}'
    p = PartnerVisualProfile(user_id=user.id, partner_name=user.partner_name or 'Moones', fictional_age=_age_from_user(user), gender_presentation=presentation, ethnicity_or_regional_style='Iranian / Persian regional style, fictional person', face_description=face, hair_description=hair, eye_description=f'{traits["eye_shape"]}, {traits["eye_color"]}', skin_description=f'{traits["skin_tone"]}, natural realistic skin texture', body_description=f'{traits["build"]}, adult body proportions', height_impression=traits['height'], default_style='realistic candid smartphone photography', distinguishing_details=f'{traits["feature"]}; {grooming}; no celebrity resemblance', default_city='Tehran', base_seed=seed, profile_json={**traits,'grooming':grooming,'interests': user.partner_interests or ''}, source='derived')
    db.add(p); db.flush()
    try:
        from app.services.image_pipeline_v2 import ensure_visual_profile_v2
        return ensure_visual_profile_v2(db, user, p)
    except Exception:
        return p


def stable_identity_descriptor(profile: PartnerVisualProfile) -> dict:
    traits = profile.profile_json or {}
    return {
        'name': profile.partner_name, 'age': profile.fictional_age, 'gender_presentation': profile.gender_presentation,
        'face_shape': traits.get('face_shape'), 'jaw_chin_geometry': traits.get('jaw'), 'cheekbone_structure': profile.face_description,
        'eyebrow_shape_spacing': traits.get('eyebrow_shape'), 'eye_shape_color_spacing': profile.eye_description,
        'nose_bridge_tip_width': traits.get('nose'), 'lip_shape_proportions': traits.get('feature'),
        'hairline_length_texture_color': profile.hair_description, 'skin_tone_details': profile.skin_description,
        'stable_distinguishing_details': profile.distinguishing_details, 'stable_body_build': profile.body_description, 'height_impression': profile.height_impression,
    }

def identity_fingerprint(profile: PartnerVisualProfile) -> str:
    data=json.dumps(stable_identity_descriptor(profile), ensure_ascii=False, sort_keys=True, separators=(',',':'))
    return hashlib.sha256(data.encode()).hexdigest()

def identity_prompt_block(profile: PartnerVisualProfile) -> str:
    d=stable_identity_descriptor(profile)
    return ('Identity continuity: Identity constraints (stable fictional person, exact descriptor set): '
            f"face shape {d['face_shape']}; jaw/chin geometry {d['jaw_chin_geometry']}; cheekbone/facial structure {d['cheekbone_structure']}; "
            f"eyebrow shape and spacing {d['eyebrow_shape_spacing']}; eye shape, color, and spacing {d['eye_shape_color_spacing']}; "
            f"nose bridge/tip/width {d['nose_bridge_tip_width']}; lip shape and relative proportions {d['lip_shape_proportions']}; "
            f"hairline/length/texture/color {d['hairline_length_texture_color']}; skin tone and stable details {d['skin_tone_details']}; "
            f"stable distinguishing details {d['stable_distinguishing_details']}; stable body/build {d['stable_body_build']}; {d['height_impression']}; no celebrity resemblance.")

def adult_eligible(db: Session, user: User, profile: PartnerVisualProfile) -> tuple[bool,str|None]:
    minimum, age_ok = _parse_minimum_age(getattr(user, 'partner_age_range', None))
    if not age_ok: return False, 'partner_under_21_or_ambiguous'
    if profile.fictional_age != (minimum or profile.fictional_age): profile.fictional_age = minimum or profile.fictional_age
    if 'user_addons' not in inspect(db.bind).get_table_names(): return False, 'adult_image_addon_required'
    if not user_owns_addon(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK): return False, 'adult_image_addon_required'
    if not user_addon_enabled(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK): return False, 'adult_image_addon_disabled'
    return True, None

def _conversation_text(user_request: str, recent_conversation=None) -> str:
    parts: list[str] = []
    for m in recent_conversation or []:
        content = getattr(m, 'content', m if isinstance(m, str) else '')
        if content:
            parts.append(str(content))
    parts.append(user_request or '')
    return '\n'.join(parts[-10:])

def extract_image_context(user_request: str, recent_conversation=None) -> ExtractedImageContext:
    state = resolve_visual_scene_state(user_request, recent_conversation)
    return ExtractedImageContext(state.scene, state.pose, state.mood, state.daypart, state.visual_corrections, bool(state.visual_corrections))

def _scene(text: str, extracted: ExtractedImageContext | None = None) -> tuple[str,str]:
    city='Tehran'
    for c in ['Tehran','Isfahan','Shiraz','Rasht','Mashhad','تهران','اصفهان','شیراز','رشت','مشهد']:
        if c in text: city = {'تهران':'Tehran','اصفهان':'Isfahan','شیراز':'Shiraz','رشت':'Rasht','مشهد':'Mashhad'}.get(c,c)
    if extracted and extracted.scene_context:
        return 'contextual_scene', f'{extracted.scene_context} in {city}'
    if 'کافه' in text: return 'cafe', f'a cozy cafe in {city}'
    if 'خانه' in text or 'خونه' in text: return 'home_selfie', f'a private home interior in {city}'
    if 'خیابان' in text or 'شهر' in text: return 'urban_street', f'an urban street in {city}'
    if 'الان' in text: return 'current_moment', f'current routine location in {city}'
    return 'selfie', f'a realistic daily-life setting in {city}'

def retrieve_positive_examples(db: Session, user_id: int, content_mode: str, scene_type: str, limit: int=3) -> list[ImageGenerationJob]:
    return list(db.scalars(select(ImageGenerationJob).join(ImageGenerationFeedback, ImageGenerationFeedback.job_id==ImageGenerationJob.id).where(ImageGenerationJob.user_id==user_id, ImageGenerationJob.status=='sent', ImageGenerationJob.content_mode==content_mode, ImageGenerationFeedback.rating=='positive', ImageGenerationJob.prompt.is_not(None)).order_by(ImageGenerationFeedback.created_at.desc()).limit(limit)).all())

def build_image_prompt(db: Session, *, user: User, user_request: str, recent_conversation=None, relevant_memories=None, relationship_state=None, mood: str|None=None, time_context=None, routine_slot=None, visual_profile: PartnerVisualProfile|None=None, current_location: str|None=None, adult_mode_requested: bool|None=None) -> ImagePromptResult:
    visual_profile = visual_profile or ensure_visual_profile(db, user)
    req = user_request or ''
    if any(w in req for w in HARD_BLOCK):
        return ImagePromptResult('', NORMAL_NEGATIVE_PROMPT, 'blocked', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason='hard_boundary')
    try:
        soft_enabled = __import__('app.services.settings_service', fromlist=['SettingsService']).SettingsService().get_bool(db, 'image_generation.soft_safety_enabled', True)
    except Exception:
        soft_enabled = True
    if soft_enabled and any(w in req.lower() for w in ['self harm','suicide','خودکشی','خودزنی','نفرت','hate']):
        return ImagePromptResult('', NORMAL_NEGATIVE_PROMPT, 'blocked', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason='soft_safety')
    adult_intent = resolve_adult_visual_intent(req)
    if adult_intent.denial_reason:
        return ImagePromptResult('', ADULT_NEGATIVE_PROMPT, 'blocked', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason=adult_intent.denial_reason, adult_visual_intent=adult_intent.intent_type, adult_nudity_level=adult_intent.nudity_level, adult_body_emphasis=adult_intent.body_emphasis)
    if adult_mode_requested is True and not adult_intent.is_adult:
        adult_intent = AdultVisualIntent(is_adult=True, intent_type='adult_general', nudity_level='suggestive', explicit_current_request=False, requested_clothing_state='adult styling requested by the user', requires_private_setting=False)
    adult = adult_intent.is_adult if adult_mode_requested is None else bool(adult_mode_requested)
    if adult:
        try:
            adult_enabled = __import__('app.services.settings_service', fromlist=['SettingsService']).SettingsService().get_bool(db, 'image_generation.adult_enabled', True)
        except Exception:
            adult_enabled = True
        if not adult_enabled:
            return ImagePromptResult('', ADULT_NEGATIVE_PROMPT, 'adult', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason='adult_generation_globally_disabled')
    if adult:
        ok, reason = adult_eligible(db, user, visual_profile)
        if not ok: return ImagePromptResult('', ADULT_NEGATIVE_PROMPT, 'adult', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason=reason)
    stored_visual_state = None
    for mem in relevant_memories or []:
        if getattr(mem, 'type', None) == 'visual_scene_state':
            try: stored_visual_state = json.loads(mem.content or '{}')
            except Exception: stored_visual_state = None
            break
    visual_state = resolve_visual_scene_state(req, recent_conversation, stored_visual_state)
    current_scene = _scene_from_text(req)
    explicit_current_location = bool(current_scene.get('location'))
    continuity_requested = _explicit_scene_continuity_requested(req)
    stale_scene_reset = False
    stale_scene_reset_reason = None
    if adult_intent.explicit_current_request and adult_intent.intent_type in {'full_nudity','topless','lingerie'}:
        visual_state.clothing = None
    if adult_intent.has_explicit_adult_scene_terms:
        stale_scene_reset = True
        stale_scene_reset_reason = 'explicit_adult_scene_override'
        if any(x in adult_intent.scene_override for x in ['bed', 'bedroom']):
            visual_state.scene = 'private fictional bedroom interior with a clearly visible bed'
            visual_state.location = 'private fictional bedroom interior'
            visual_state.environment_type = 'home'
        elif 'private_home' in adult_intent.scene_override:
            visual_state.scene = 'private fictional home interior'
            visual_state.location = 'private fictional home interior'
            visual_state.environment_type = 'home'
        visual_state.subject_action = None
        visual_state.held_objects = []
        visual_state.activity = None
        if adult_intent.pose_override:
            visual_state.pose = 'lying or reclining on a bed with natural body posture supported by the bed' if any(x in adult_intent.pose_override for x in ['on_bed','lying','reclining']) else visual_state.pose
    elif adult_intent.requires_private_setting and not explicit_current_location and not continuity_requested and visual_state.environment_type in PUBLIC_ENVIRONMENTS:
        stale_scene_reset = True
        stale_scene_reset_reason = f'reset stale public scene {visual_state.environment_type} for explicit private adult nudity request'
        visual_state.scene = 'private fictional bedroom or private home interior'
        visual_state.environment_type = 'home'
        visual_state.location = 'private fictional bedroom or private home interior'
        visual_state.subject_action = None
        visual_state.held_objects = []
        visual_state.activity = None
        if visual_state.pose and any(x in visual_state.pose.lower() for x in ['sitting', 'walking']):
            visual_state.pose = None
    elif adult_intent.requires_private_setting and not explicit_current_location and not visual_state.location:
        visual_state.scene = 'private fictional bedroom or private home interior'
        visual_state.environment_type = 'home'
        visual_state.location = 'private fictional bedroom or private home interior'
    explicit_close_framing = _explicit_close_framing_requested_by_user(req)
    if not explicit_close_framing and visual_state.camera_request in {'selfie', 'portrait'}:
        visual_state.camera_request = None
    extracted = ExtractedImageContext(visual_state.scene, visual_state.pose, visual_state.mood, visual_state.daypart, visual_state.visual_corrections, bool(visual_state.visual_corrections))
    recent_jobs=list(db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user.id, ImageGenerationJob.status=='sent').order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(10)).all())
    composition = plan_composition(visual_state, recent_jobs)
    if adult_intent.intent_type in {'full_nudity', 'topless'} and not explicit_close_framing:
        composition = CompositionPlan(f'adult-{adult_intent.intent_type}','three-quarter or full-body candid','natural eye-level phone angle','three-quarter or full-body candid framing, subject occupies about 45%–70% of the frame, visible torso and requested body area clearly visible','portrait','private indoor environment visible but secondary','body posture must be visible; avoid face-only framing; avoid overly distant shot; do not crop the upper torso',False,'45%–70%','camera positioned a few steps away',[],1024,1280)
    scene_type, location = _scene(req, extracted)
    if visual_state.location:
        location=visual_state.location
        scene_type=visual_state.environment_type or scene_type
    local_hour = getattr(time_context, 'local_hour', None)
    hour = local_hour if local_hour is not None else datetime.utcnow().hour
    lower=req.lower()
    if 'dawn' in lower or 'سپیده' in lower or 'طلوع' in lower: lighting='dawn blue-gold light'
    elif 'morning' in lower or 'صبح' in lower: lighting='soft morning light'
    elif 'noon' in lower or 'ظهر' in lower: lighting='bright noon daylight'
    elif 'afternoon' in lower or 'بعدازظهر' in lower: lighting='gentle afternoon light'
    elif 'sunset' in lower or 'غروب' in lower or 'evening' in lower or 'عصر' in lower: lighting='warm evening sunset light'
    elif 'late night' in lower or 'نیمه شب' in lower: lighting='late night low warm light'
    elif 'night' in lower or 'شب' in lower: lighting='night city/indoor lighting'
    else: lighting = 'dawn blue-gold light' if 5 <= hour < 7 else ('soft morning light' if 7 <= hour < 11 else ('bright noon daylight' if 11 <= hour < 14 else ('gentle afternoon light' if 14 <= hour < 18 else ('warm evening sunset light' if 18 <= hour < 21 else 'late night low warm light'))))
    if extracted.time_context == 'late night':
        lighting = 'late night low warm light'
    elif extracted.time_context == 'night':
        lighting = 'soft night indoor lighting'
    camera = f'{composition.shot_type}, {composition.camera_angle}, {composition.subject_scale}, {composition.environment_visibility}, {composition.pose_constraints}'
    if re.search(r'سلفی|selfie', req.lower()):
        camera = 'natural casual selfie requested by the user, head-and-shoulders to half body allowed because selfie was explicitly requested, no readable text in background'
    pose = extracted.pose_context or composition.pose_constraints or 'relaxed natural pose'
    mood_block = extracted.mood_context or (mood or getattr(user, 'current_mood', '') or 'warm natural mood')
    wardrobe = visual_state.clothing or (adult_intent.requested_clothing_state if adult_intent.is_adult else 'tasteful casual clothing suited to the scene')
    examples = retrieve_positive_examples(db, user.id, 'adult' if adult else 'normal', scene_type)
    # Sanitized style learning only: never copy prior locations, nudity, wardrobe, pose, activity, objects, or safety wording.
    example_note = 'realistic candid smartphone style, natural lighting and color preference' if examples else ''
    scene_based_prompt = _is_scene_based_request(visual_state) and not explicit_close_framing
    subject_block = f'A realistic candid photo of {visual_profile.partner_name}, a fictional adult age {visual_profile.fictional_age}, gender presentation: {visual_profile.gender_presentation}. ' + identity_prompt_block(visual_profile)
    grounded_location = location if (extracted.scene_context or visual_state.location) else (current_location or location)
    context_block = f'Current physical state and scene: {grounded_location}; {pose}.'
    pose_block = 'Exact pose relationship: show the torso and body posture clearly; avoid generic upright portrait framing.'
    if adult_intent.requested_body_framing:
        pose_block += f' Adult framing: {adult_intent.requested_body_framing}.'
    if not explicit_close_framing and not adult_intent.requested_body_framing:
        pose_block += ' Default to a medium-wide or wide environmental candid composition with the camera positioned a few steps away: visible torso and body posture, subject occupies about 25%–45% of the frame, readable surrounding environment, subject must not fill most of the frame, avoid direct eye contact with camera unless requested, and avoid face-only or shoulders-up framing unless explicitly requested.'
    if pose and 'reclining' in pose.lower():
        pose_block += ' If reclining, clearly show the supporting furniture and body posture: visible sofa/bed cushions, body lying back naturally with believable contact, not sitting upright, not standing, not a formal portrait.'
    if adult_intent.has_explicit_adult_scene_terms and any(x in adult_intent.scene_override for x in ['bed', 'bedroom']):
        pose_block += ' Adult scene override: private fictional bedroom interior, lying or reclining on a bed, visible bed, bedding, and pillows, natural body posture supported by the bed; discard incompatible prior public-scene context.'
    objects = ', '.join(visual_state.held_objects or [])
    action = visual_state.subject_action or visual_state.activity or 'natural daily-life moment'
    mood_prompt_block = f"Mood and activity: {mood_block}; {action}; visible objects: {objects or 'environmental scene props as required by the activity'}."
    if adult_intent.has_explicit_adult_scene_terms and any(x in adult_intent.scene_override for x in ['bed', 'bedroom']):
        mood_prompt_block = f"Mood and activity: {mood_block}; lying or reclining on a bed in a private fictional bedroom; visible objects: bed, bedding, pillows."
    scene_framing_notes=[]
    if visual_state.environment_type == 'cafe' or visual_state.activity == 'drinking coffee':
        scene_framing_notes.append('for a cafe coffee scene, use a medium-wide environmental candid shot with visible table, visible coffee cup, visible chair, and visible surrounding cafe interior; if drinking coffee, the cup naturally interacts with the subject')
        if 'coffee cup' not in composition.required_environment_objects:
            composition.required_environment_objects.extend(['visible table','visible coffee cup','visible chair','visible surrounding cafe interior'])
    if visual_state.environment_type == 'outdoor_street' or visual_state.activity in {'eating ice cream','walking'}:
        scene_framing_notes.append('for street/outside activity, use a wide environmental candid shot with readable street context and the subject shown within the environment; include visible activity objects when applicable')
    if visual_state.environment_type in {'home'} and pose and any(x in pose.lower() for x in ['reclining','lying back']):
        scene_framing_notes.append('for sofa/bed lounging, use a relaxed reclined composition with visible supporting furniture and clear body posture')
    scene_framing = ('; ' + '; '.join(scene_framing_notes)) if scene_framing_notes else ''
    camera_block = f'Composition and camera: {camera}; orientation {composition.orientation}; natural photogenic candid smartphone composition, not a centered passport-style crop{scene_framing}.'
    if scene_based_prompt:
        camera_block += ' Identity continuity: full stable descriptor follows; keep identity subordinate to the scene framing.'
    scene_quality = 'coherent furniture, perspective, and room geometry' if visual_state.environment_type == 'home' else 'scene-specific authentic environment details, no home-interior assumptions'
    quality_block = f'Attractive but natural adult appearance, harmonious realistic facial proportions, expressive symmetrical eyes, natural healthy skin texture with subtle realistic skin detail, polished grooming and well-kept hair, flattering but believable lighting, relaxed authentic facial expression, {scene_quality}.'
    constraints = list(extracted.explicit_visual_constraints)
    if not explicit_close_framing:
        constraints.append('composition instructions override portrait bias from the identity description')
        constraints.append('no close-up portrait, no tight crop, no tight close-up, no face filling frame, no headshot, no shoulders-only portrait, no passport photo, no generic selfie close-up, no centered beauty portrait, no direct-to-camera beauty shot, no medium-close portrait, no face-dominant composition')
    if extracted.pose_context:
        constraints.append('avoid generic upright portrait and default close-up looking-at-camera pose unless explicitly requested')
    hard_constraints_block = f'{ANTI_TEXT_POSITIVE_CONSTRAINT} No real person, no celebrity resemblance, no exaggerated beauty, no doll-like face, no plastic skin, no extreme makeup, no unrealistic body proportions, no metadata. ' + '; '.join(constraints)
    prompt_parts = [context_block, pose_block, mood_prompt_block, camera_block, f'Lighting: {lighting}.', subject_block, f'Clothing: {wardrobe}.', quality_block, hard_constraints_block] if scene_based_prompt else [subject_block, context_block, pose_block, mood_prompt_block, camera_block, f'Lighting: {lighting}.', f'Clothing: {wardrobe}.', quality_block, hard_constraints_block]
    prompt = ' '.join(prompt_parts) + ' '
    issues=validate_prompt_contradictions(prompt, visual_state, composition)
    if issues:
        raise ImagePromptInvariantError(issues)
    scene_specific_negative = '' if explicit_close_framing else ENVIRONMENTAL_NEGATIVE_TERMS
    negative_parts = [ADULT_NEGATIVE_PROMPT if adult else NORMAL_NEGATIVE_PROMPT]
    if scene_specific_negative: negative_parts.append(scene_specific_negative)
    if adult_intent.intent_type == 'topless':
        negative_parts.append('lower-body nudity, full nudity, underwear-only conversion, shirt or bra covering requested visible breasts')
    elif adult_intent.intent_type == 'lingerie':
        negative_parts.append('full nudity, topless conversion, no underwear')
    elif adult_intent.intent_type == 'suggestive':
        negative_parts.append('explicit nudity beyond request')
    elif not adult:
        negative_parts.append('nudity, topless, lingerie, explicit sexual framing')
    negative_prompt = ', '.join(dict.fromkeys([x.strip() for x in ', '.join(negative_parts).split(',') if x.strip()]))
    if adult: prompt += 'Consensual fictional adult imagery only; all subjects are clearly 21+ fictional adults. '
    if example_note: prompt += f'Sanitized style preference: {example_note}. '
    invariant_codes = validate_plan_invariants(None) + validate_prompt_invariants(None, prompt, negative_prompt)
    if invariant_codes:
        raise ImagePromptInvariantError(invariant_codes)
    logger.info("IMAGE_INTENT_RESOLVED user_id=%s adult_intent=%s nudity=%s", user.id, adult_intent.intent_type, adult_intent.nudity_level)
    logger.info("IMAGE_CONTEXT_MERGED user_id=%s fields=%s", user.id, ['scene','composition','wardrobe'])
    logger.info("IMAGE_SAFETY_DECIDED user_id=%s decision=%s reason=%s", user.id, 'allow', None)
    logger.info("IMAGE_SCENE_PLAN_RESOLVED user_id=%s env=%s pose=%s", user.id, visual_state.environment_type, visual_state.pose)
    logger.info("IMAGE_COMPOSITION_RESOLVED user_id=%s key=%s share=%s", user.id, composition.composition_key, composition.subject_frame_share)
    logger.info("IMAGE_PROMPT_INVARIANTS_VALIDATED user_id=%s codes=%s", user.id, invariant_codes)
    summary = f'scene_context={extracted.scene_context}; pose_context={extracted.pose_context}; mood_context={extracted.mood_context}; daypart={extracted.time_context or getattr(time_context, "daypart", None)}; refinement_after_critique={extracted.refinement_after_critique}; adult_visual_intent={adult_intent.intent_type}; adult_nudity_level={adult_intent.nudity_level}; adult_body_emphasis={adult_intent.body_emphasis}; stale_scene_reset={stale_scene_reset}; stale_scene_reset_reason={stale_scene_reset_reason}; final_environment_type={visual_state.environment_type}; final_pose_type={pose}; final_wardrobe_intent={wardrobe}'
    intent = ImageRequestIntent(is_image_request=True, adult_intent=adult_intent.intent_type, nudity_level=adult_intent.nudity_level, wardrobe_intent=wardrobe, body_emphasis=adult_intent.body_emphasis, scene=visual_state.scene, environment_type=visual_state.environment_type, location=grounded_location, activity=visual_state.activity, pose=pose, support_surface=visual_state.support_surface, held_objects=visual_state.held_objects, camera_mode=visual_state.camera_request, shot_type=composition.shot_type, orientation=composition.orientation, subject_frame_share=composition.subject_frame_share, lighting=lighting, daypart=visual_state.daypart, mood=visual_state.mood, continuity_action='reset' if stale_scene_reset else 'unspecified', explicit_current_fields=['scene'] if explicit_current_location else [], field_provenance={'scene': visual_state.source_role or 'routine/default', 'wardrobe': 'structured_intent' if adult_intent.is_adult else 'routine/default', 'composition': 'resolved_plan'}, field_confidence={'scene':0.9,'wardrobe':1.0,'composition':1.0}, explicit_body_visibility=adult_intent.explicit_body_visibility)
    plan = ResolvedImagePlan(intent, SafetyDecision('allow', None), visual_state, composition, wardrobe, prompt_parts, negative_prompt.split(', '), prompt, negative_prompt, composition.width, composition.height, composition.orientation, invariant_codes, PROMPT_ENGINE_VERSION, 'allow')
    return ImagePromptResult(prompt=prompt, negative_prompt=negative_prompt, content_mode='adult' if adult else 'normal', scene_type=scene_type, location=grounded_location, camera=camera, lighting=lighting, pose=pose, wardrobe=wardrobe, continuity_notes='time/routine/city continuity applied', influenced_by_job_ids=[e.id for e in examples], input_context_summary=summary, width=composition.width, height=composition.height, orientation=composition.orientation, adult_visual_intent=adult_intent.intent_type, adult_intent_explicit_current_request=adult_intent.explicit_current_request, stale_scene_reset=stale_scene_reset, stale_scene_reset_reason=stale_scene_reset_reason, final_environment_type=visual_state.environment_type, final_wardrobe_intent=wardrobe, adult_nudity_level=adult_intent.nudity_level, adult_body_emphasis=adult_intent.body_emphasis, adult_scene_override=adult_intent.scene_override, adult_pose_override=adult_intent.pose_override, final_pose_type=pose, resolved_plan=plan)
