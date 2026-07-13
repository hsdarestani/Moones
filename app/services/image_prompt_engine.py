from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import hashlib, json, logging, re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationFeedback
from app.models.user import User

IMAGE_ADDON_KEY = 'image_generation_unlock'
logger = logging.getLogger(__name__)

PROMPT_ENGINE_VERSION = 'image-prompt-v1.4.1'

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
ENVIRONMENTAL_NEGATIVE_TERMS = 'close-up portrait, tight crop, face filling frame, headshot, shoulders-only portrait, passport photo, generic selfie close-up, centered beauty portrait, tight close-up, close-up headshot, face-only crop, shoulders-up framing, passport-style crop, studio portrait'
NORMAL_NEGATIVE_PROMPT = f'blurry, lowres, deformed, bad hands, bad anatomy, cartoon, anime, {VISUAL_DEFECT_NEGATIVE_TERMS}, {ANTI_TEXT_NEGATIVE_TERMS}'
ADULT_NEGATIVE_PROMPT = f'blurry, lowres, deformed, censored, clothes, underwear, bad hands, bad anatomy, cartoon, anime, {VISUAL_DEFECT_NEGATIVE_TERMS}, {ANTI_TEXT_NEGATIVE_TERMS}'
HARD_BLOCK = ['زیر ۱۸','زیر18','نوجوان','بچه','کودک','اجبار','زور','تجاوز','بی رضایت','بی‌رضایت','محارم','حیوان','deepfake','دیپ فیک','minor','underage','coercion','non-consent','incest','bestiality','real person']
ADULT_WORDS = ['لخت','برهنه','سکسی','بزرگسال','پورن','جنسی','بدون لباس']

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

@dataclass
class ExtractedImageContext:
    scene_context: str | None = None
    pose_context: str | None = None
    mood_context: str | None = None
    time_context: str | None = None
    explicit_visual_constraints: list[str] = field(default_factory=list)
    refinement_after_critique: bool = False



def normalize_persian_text(text: str) -> str:
    t = (text or '').lower().replace('\u200c', ' ')
    t = t.replace('ي', 'ی').replace('ك', 'ک').replace('ۀ', 'ه').replace('ة', 'ه').replace('ؤ', 'و')
    t = re.sub(r'[ـًٌٍَُِّْ]', '', t)
    return re.sub(r'\s+', ' ', t).strip()

@dataclass
class CompositionPlan:
    composition_key: str
    shot_type: str
    camera_angle: str
    subject_scale: str
    orientation: str
    environment_visibility: str
    pose_constraints: str
    width: int
    height: int

@dataclass
class VisualSceneState:
    scene: str | None = None
    environment_type: str | None = None
    location: str | None = None
    subject_action: str | None = None
    held_objects: list[str] = field(default_factory=list)
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
    (r'روی تخت|رو تخت|تو تخت|تخت|زیر پتو', ('home', 'bedroom/home setting with a clearly visible bed')),
]
_ACTIVITY_PATTERNS = [
    (r'بستنی.*می ?خور|می ?خور.*بستنی|آیس ?کریم|ice cream', ('eating ice cream', 'eating and holding ice cream', ['ice cream'])),
    (r'قهوه.*می ?خور|کافه.*می ?خور|می ?نوش.*قهوه|coffee', ('drinking coffee', 'drinking coffee', ['coffee cup'])),
    (r'قدم می ?زن|راه می ?رم|پیاده روی', ('walking', 'walking naturally', [])), (r'خرید', ('shopping', 'shopping', ['shopping bag'])),
    (r'رانندگی|دارم می ?رونم|پشت فرمون', ('driving', 'driving', [])), (r'آشپزی|غذا درست', ('cooking', 'cooking', [])),
    (r'کار می ?کنم|مشغول کار', ('working', 'working', [])), (r'کتاب می ?خون|مطالعه', ('reading', 'reading', ['book'])),
    (r'ورزش|تمرین', ('exercising', 'exercising', [])), (r'نشستم|نشسته', ('sitting', 'sitting naturally', [])),
    (r'لم دادم|لم داده|دراز کشید|ولو شدم|تکیه دادم', ('reclining', 'reclining comfortably', [])),
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

def _scene_from_text(text: str) -> dict:
    nt=normalize_persian_text(text); out={}
    for pat,(env,loc) in _LOCATION_PATTERNS:
        if re.search(pat, nt):
            out.update(environment_type=env, location=loc)
            if env=='home' and 'bed' in loc: out['scene']='bedroom/home setting with a clearly visible bed'
            elif env=='home' and re.search(r'مبل|کاناپه', nt): out['scene']='home interior with a clearly visible sofa'
            elif env=='home': out['scene']='private Iranian home interior'
            elif env=='outdoor_street': out['scene']='outdoor Tehran street context with visible urban environment'
            else: out['scene']=loc
            break
    for pat,(act,action,objects) in _ACTIVITY_PATTERNS:
        if re.search(pat, nt): out.update(activity=act, subject_action=action, held_objects=objects); break
    for f in ['pose','mood','daypart','clothing','camera_request']:
        v=_field_value(f, text)
        if v: out[f]=v
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
        return CompositionPlan('selfie','natural selfie','handheld phone angle','head-and-shoulders to half body allowed because selfie was explicitly requested','portrait','natural background visible but secondary','natural selfie framing, not overly posed',1024,1280)
    if state.camera_request=='portrait':
        return CompositionPlan('portrait','requested portrait photo','natural phone angle','portrait or close framing allowed because explicitly requested','portrait','simple natural background visible but secondary','portrait framing requested by user',1024,1280)
    if any(x in pose_scene for x in ['reclining','lying','sofa','bed']):
        return CompositionPlan('seated candid' if 'seated' in pose_scene else 'three-quarter','environmental medium-wide candid','natural smartphone perspective','three-quarter body, subject roughly 30% to 60% of the frame','landscape','sofa/bed and surrounding home environment clearly visible','body positioned along visible supporting furniture, believable weight/contact with cushions, not sitting upright, not standing, no cropped furniture',1280,1024)
    if outdoor or state.activity in {'eating ice cream','walking','shopping'}:
        choices=[('three-quarter','three-quarter environmental candid','natural eye-level phone angle','three-quarter body, subject roughly 30% to 60% of the frame','portrait'),('full-body','full-body environmental candid','slightly wider phone angle','full body, subject roughly 30% to 60% of the frame','portrait'),('environmental wide','environmental wide candid','street-level phone angle','person visible within environment, subject roughly 30% to 50% of the frame','landscape')]
        choice=next((c for c in choices if c[0] not in recent_keys[-10:]), choices[0])
        return CompositionPlan(choice[0],choice[1],choice[2],choice[3],choice[4],'environment clearly visible and readable around the subject','natural action pose with visible object interaction when relevant; avoid face-only or shoulders-up framing',1280 if choice[4]=='landscape' else 1024,1024 if choice[4]=='landscape' else 1280)
    if state.camera_request=='full body' or 'standing' in pose_scene:
        return CompositionPlan('full-body','full-body or three-quarter candid','natural eye-level phone angle','full body or three-quarter body','portrait','environment visible enough for context','natural standing posture',1024,1280)
    if scene_based:
        return CompositionPlan('environmental medium','medium / three-quarter environmental candid','natural smartphone perspective','medium to three-quarter body, subject roughly 30% to 60% of the frame','landscape','scene environment visibly readable from the framing','activity-specific natural pose; avoid face-only or shoulders-up framing',1280,1024)
    return CompositionPlan('environmental candid','medium environmental candid daily-life photo','natural phone angle','medium to three-quarter body, subject roughly 30% to 60% of the frame','portrait','believable environment visible and readable','relaxed natural pose; avoid face-only or shoulders-up framing unless explicitly requested',1024,1280)

def validate_prompt_contradictions(prompt: str, state: VisualSceneState, composition: CompositionPlan) -> list[str]:
    p=prompt.lower(); issues=[]
    if state.environment_type in {'outdoor_street','park','beach','travel'} and any(x in p for x in ['home interior','sofa','bedroom','indoor lighting']): issues.append('outdoor_home_contradiction')
    if state.activity=='eating ice cream' and not ('ice cream' in p and any(x in p for x in ['holding','eating','licking'])): issues.append('missing_ice_cream_interaction')
    if state.camera_request=='full body' and any(x in p for x in ['close-up','headshot']): issues.append('full_body_closeup_contradiction')
    if state.pose and 'reclining' in state.pose and not any(x in p for x in ['sofa','bed','cushion','furniture']): issues.append('reclining_without_support')
    return issues

def is_explicit_image_request(text: str) -> bool:
    t = re.sub(r'\s+', ' ', text or '').strip().lower()
    if not t:
        return False
    # Avoid broad matches for discussion/metadata about photos; require an
    # imperative/request verb near photo/image/selfie terms.
    media = r'(?:عکس|تصویر|سلفی|عکست|عکس\s*خودتو|تصویر\s+از\s+خودت)'
    request = r'(?:بفرست|بفرستی|بده|بدی|بساز|بسازی|درست\s+کن|درست\s+کنی|ارسال\s+کن|نشونم\s+بده)'
    polite = r'(?:یه|یک|لطفاً|لطفا|میشه|می\s*شه|برام|از\s+خودت|خودتو|خودت|رو|را|هم)?'
    patterns = [
        rf'{polite}\s*{media}\s*(?:از\s+خودت|خودتو|خودت|برام|رو|را)?\s*{request}',
        rf'{request}\s*(?:یه|یک)?\s*{media}',
        r'عکس\s+(?:توی|تو|در)\s+',
        r'عکس\s+الان',
    ]
    return any(re.search(p, t) for p in patterns)

def adult_requested(text: str) -> bool:
    return any(w in (text or '').lower() for w in ADULT_WORDS)

def _age_from_user(user: User) -> int:
    raw = str(user.partner_age_range or '')
    nums = [int(x) for x in re.findall(r'\d+', raw)]
    return max([21] + nums)

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
    if existing: return existing
    seed = int(hashlib.sha256(f'{user.id}:{user.partner_name}:{user.partner_gender}'.encode()).hexdigest()[:8], 16) % 2147483647
    gender=(user.partner_gender or 'feminine').lower()
    presentation = 'masculine' if gender in {'male','man','masculine','مرد'} else ('neutral' if gender in {'neutral','nonbinary','non-binary'} else 'feminine')
    traits={k:_pick(seed,k) for k in TRAIT_BANK}
    grooming = {'feminine':'tasteful natural makeup, styled well-kept hair, polished but believable appearance', 'masculine':f'groomed hair, {traits["beard"]}, polished but believable appearance', 'neutral':'polished gender-neutral presentation with neat hair and believable styling'}[presentation]
    face=f'{traits["face_shape"]}, {traits["jaw"]}, {traits["eyebrow_shape"]}, {traits["nose"]}, {traits["feature"]}'
    hair=f'{traits["hair_color"]}, {traits["hair_texture"]}, {traits["hair_style"]}'
    p = PartnerVisualProfile(user_id=user.id, partner_name=user.partner_name or 'Moones', fictional_age=max(21,_age_from_user(user)), gender_presentation=presentation, ethnicity_or_regional_style='Iranian / Persian regional style, fictional person', face_description=face, hair_description=hair, eye_description=f'{traits["eye_shape"]}, {traits["eye_color"]}', skin_description=f'{traits["skin_tone"]}, natural realistic skin texture', body_description=f'{traits["build"]}, adult body proportions', height_impression=traits['height'], default_style='realistic candid smartphone photography', distinguishing_details=f'{traits["feature"]}; {grooming}; no celebrity resemblance', default_city='Tehran', base_seed=seed, profile_json={**traits,'grooming':grooming,'interests': user.partner_interests or ''}, source='derived')
    db.add(p); db.flush(); return p

def adult_eligible(user: User, profile: PartnerVisualProfile) -> tuple[bool,str|None]:
    if profile.fictional_age < 21: return False, 'partner_under_21_or_ambiguous'
    if not getattr(user, 'adult_content_confirmed', False): return False, 'adult_confirmation_required'
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
    adult = adult_requested(req) if adult_mode_requested is None else adult_mode_requested
    if adult:
        try:
            adult_enabled = __import__('app.services.settings_service', fromlist=['SettingsService']).SettingsService().get_bool(db, 'image_generation.adult_enabled', True)
        except Exception:
            adult_enabled = True
        if not adult_enabled:
            return ImagePromptResult('', ADULT_NEGATIVE_PROMPT, 'adult', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason='adult_generation_disabled')
    if adult:
        ok, reason = adult_eligible(user, visual_profile)
        if not ok: return ImagePromptResult('', ADULT_NEGATIVE_PROMPT, 'adult', 'blocked', '', '', '', '', '', '', safety_decision='block', safety_reason=reason)
    stored_visual_state = None
    for mem in relevant_memories or []:
        if getattr(mem, 'type', None) == 'visual_scene_state':
            try: stored_visual_state = json.loads(mem.content or '{}')
            except Exception: stored_visual_state = None
            break
    visual_state = resolve_visual_scene_state(req, recent_conversation, stored_visual_state)
    explicit_close_framing = _explicit_close_framing_requested_by_user(req)
    if not explicit_close_framing and visual_state.camera_request in {'selfie', 'portrait'}:
        visual_state.camera_request = None
    extracted = ExtractedImageContext(visual_state.scene, visual_state.pose, visual_state.mood, visual_state.daypart, visual_state.visual_corrections, bool(visual_state.visual_corrections))
    recent_jobs=list(db.scalars(select(ImageGenerationJob).where(ImageGenerationJob.user_id==user.id, ImageGenerationJob.status=='sent').order_by(ImageGenerationJob.sent_at.desc(), ImageGenerationJob.id.desc()).limit(10)).all())
    composition = plan_composition(visual_state, recent_jobs)
    scene_type, location = _scene(req, extracted)
    if visual_state.location:
        location=visual_state.location
        scene_type=visual_state.environment_type or scene_type
    hour = getattr(time_context, 'local_hour', None) or (datetime.utcnow().hour)
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
    wardrobe = visual_state.clothing or ('tasteful casual clothing suited to the scene' if not adult else 'fictional consenting adult erotic styling requested by the user')
    examples = retrieve_positive_examples(db, user.id, 'adult' if adult else 'normal', scene_type)
    example_note = '; '.join([(e.prompt or '')[:160] for e in examples])
    subject_block = f'A realistic candid photo of {visual_profile.partner_name}, a fictional adult age {visual_profile.fictional_age}, gender presentation: {visual_profile.gender_presentation}, preserving established facial identity: {visual_profile.face_description}, {visual_profile.hair_description}, {visual_profile.eye_description}, {visual_profile.skin_description}, {visual_profile.body_description}, {visual_profile.height_impression}, {visual_profile.distinguishing_details}.'
    grounded_location = location if (extracted.scene_context or visual_state.location) else (current_location or location)
    context_block = f'Current physical state and scene: {grounded_location}; {pose}.'
    pose_block = 'Exact pose relationship: show the torso and body posture clearly; avoid generic upright portrait framing.'
    if not explicit_close_framing:
        pose_block += ' Default to an environmental candid composition: subject occupies roughly 30% to 60% of the frame, environment remains visibly readable, and the subject must not fill most of the frame; avoid face-only or shoulders-up framing unless explicitly requested.'
    if pose and 'reclining' in pose.lower():
        pose_block += ' If reclining, clearly show the supporting furniture and body posture: visible sofa/bed cushions, body lying back naturally with believable contact, not sitting upright, not standing, not a formal portrait.'
    objects = ', '.join(visual_state.held_objects or [])
    action = visual_state.subject_action or visual_state.activity or 'natural daily-life moment'
    mood_prompt_block = f"Mood and activity: {mood_block}; {action}; visible objects: {objects or 'none'}."
    scene_framing_notes=[]
    if visual_state.environment_type == 'cafe' or visual_state.activity == 'drinking coffee':
        scene_framing_notes.append('for a cafe coffee scene, use a medium or wider candid shot with visible table, cup, chair, and some cafe background environment')
    if visual_state.environment_type == 'outdoor_street' or visual_state.activity in {'eating ice cream','walking'}:
        scene_framing_notes.append('for street/outside activity, use a wider environmental candid shot with readable street context around the subject')
    if visual_state.environment_type in {'home'} and pose and any(x in pose.lower() for x in ['reclining','lying back']):
        scene_framing_notes.append('for sofa/bed lounging, use a relaxed reclined composition with visible supporting furniture and clear body posture')
    scene_framing = ('; ' + '; '.join(scene_framing_notes)) if scene_framing_notes else ''
    camera_block = f'Composition and camera: {camera}; orientation {composition.orientation}; natural photogenic candid smartphone composition, not a centered passport-style crop{scene_framing}.'
    scene_quality = 'coherent furniture, perspective, and room geometry' if visual_state.environment_type == 'home' else 'scene-specific authentic environment details, no home-interior assumptions'
    quality_block = f'Attractive but natural adult appearance, harmonious realistic facial proportions, expressive symmetrical eyes, natural healthy skin texture with subtle realistic skin detail, polished grooming and well-kept hair, flattering but believable lighting, relaxed authentic facial expression, {scene_quality}.'
    constraints = list(extracted.explicit_visual_constraints)
    if not explicit_close_framing:
        constraints.append('composition instructions override portrait bias from the identity description')
        constraints.append('no close-up portrait, no tight crop, no tight close-up, no face filling frame, no headshot, no shoulders-only portrait, no passport photo, no generic selfie close-up, no centered beauty portrait')
    if extracted.pose_context:
        constraints.append('avoid generic upright portrait and default close-up looking-at-camera pose unless explicitly requested')
    hard_constraints_block = f'{ANTI_TEXT_POSITIVE_CONSTRAINT} No real person, no celebrity resemblance, no exaggerated beauty, no doll-like face, no plastic skin, no extreme makeup, no unrealistic body proportions, no metadata. ' + '; '.join(constraints)
    prompt = ' '.join([subject_block, context_block, pose_block, mood_prompt_block, camera_block, f'Lighting: {lighting}.', f'Clothing: {wardrobe}.', quality_block, hard_constraints_block]) + ' '
    issues=validate_prompt_contradictions(prompt, visual_state, composition)
    if issues:
        prompt += ' Mandatory correction: resolve prompt contradictions: ' + ', '.join(issues) + '. '
    scene_specific_negative = '' if explicit_close_framing else ENVIRONMENTAL_NEGATIVE_TERMS
    if adult: prompt += 'Consensual fictional adult imagery only; all subjects are clearly 21+ fictional adults. '
    if example_note: prompt += f'Style reference from prior liked outputs summarized: {example_note}'
    logger.info("IMAGE_VISUAL_STATE_RESOLVED user_id=%s scene=%s pose=%s activity=%s mood=%s source_role=%s source_message_id=%s orientation=%s width=%s height=%s fallback_fields=%s", user.id, visual_state.scene, visual_state.pose, visual_state.activity, visual_state.mood, visual_state.source_role, visual_state.source_message_id, composition.orientation, composition.width, composition.height, visual_state.fallback_fields); logger.info("IMAGE_COMPOSITION_PLANNED user_id=%s orientation=%s shot_type=%s subject_scale=%s", user.id, composition.orientation, composition.shot_type, composition.subject_scale);
    if visual_state.visual_corrections: logger.info("IMAGE_REFINEMENT_CONSTRAINTS_APPLIED user_id=%s count=%s", user.id, len(visual_state.visual_corrections))
    summary = f'scene_context={extracted.scene_context}; pose_context={extracted.pose_context}; mood_context={extracted.mood_context}; daypart={extracted.time_context or getattr(time_context, "daypart", None)}; refinement_after_critique={extracted.refinement_after_critique}'
    return ImagePromptResult(prompt=prompt, negative_prompt=((ADULT_NEGATIVE_PROMPT if adult else NORMAL_NEGATIVE_PROMPT) + ((', ' + scene_specific_negative) if scene_specific_negative else '')), content_mode='adult' if adult else 'normal', scene_type=scene_type, location=grounded_location, camera=camera, lighting=lighting, pose=pose, wardrobe=wardrobe, continuity_notes='time/routine/city continuity applied', influenced_by_job_ids=[e.id for e in examples], input_context_summary=summary, width=composition.width, height=composition.height, orientation=composition.orientation)
