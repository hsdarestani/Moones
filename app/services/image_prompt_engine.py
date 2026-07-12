from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import hashlib, re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationFeedback
from app.models.user import User

IMAGE_ADDON_KEY = 'image_generation_unlock'
PROMPT_ENGINE_VERSION = 'image-prompt-v1.0.0'
NORMAL_NEGATIVE_PROMPT = 'blurry, lowres, deformed, ugly, text, watermark, bad hands, bad anatomy, extra fingers, duplicate limbs, cartoon, anime'
ADULT_NEGATIVE_PROMPT = 'blurry, lowres, deformed, ugly, censored, clothes, underwear, text, watermark, bad hands, bad anatomy, cartoon, anime'
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

def ensure_visual_profile(db: Session, user: User) -> PartnerVisualProfile:
    existing = db.scalar(select(PartnerVisualProfile).where(PartnerVisualProfile.user_id == user.id))
    if existing: return existing
    seed = int(hashlib.sha256(f'{user.id}:{user.partner_name}'.encode()).hexdigest()[:8], 16) % 2147483647
    p = PartnerVisualProfile(user_id=user.id, partner_name=user.partner_name or 'Moones', fictional_age=max(21,_age_from_user(user)), gender_presentation=user.partner_gender or 'feminine', ethnicity_or_regional_style='Iranian / Persian regional style, fictional person', face_description='consistent oval face, natural features, warm expression', hair_description='dark natural hair styled consistently', eye_description='expressive dark eyes', skin_description='natural realistic skin texture', body_description='adult body proportions, consistent build', height_impression='average height impression', default_style='realistic candid smartphone photography', distinguishing_details='subtle familiar smile; no celebrity resemblance', default_city='Tehran', base_seed=seed, profile_json={'interests': user.partner_interests or ''}, source='derived')
    db.add(p); db.flush(); return p

def adult_eligible(user: User, profile: PartnerVisualProfile) -> tuple[bool,str|None]:
    if profile.fictional_age < 21: return False, 'partner_under_21_or_ambiguous'
    if not getattr(user, 'adult_content_confirmed', False): return False, 'adult_confirmation_required'
    return True, None

def _scene(text: str) -> tuple[str,str]:
    city='Tehran'
    for c in ['Tehran','Isfahan','Shiraz','Rasht','Mashhad','تهران','اصفهان','شیراز','رشت','مشهد']:
        if c in text: city = {'تهران':'Tehran','اصفهان':'Isfahan','شیراز':'Shiraz','رشت':'Rasht','مشهد':'Mashhad'}.get(c,c)
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
    scene_type, location = _scene(req)
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
    camera = 'realistic 50mm portrait photo, shallow depth of field, candid composition'
    pose = 'relaxed natural pose, looking at camera'
    wardrobe = 'everyday stylish outfit appropriate to the scene' if not adult else 'fictional consenting adult erotic styling requested by the user'
    examples = retrieve_positive_examples(db, user.id, 'adult' if adult else 'normal', scene_type)
    example_note = '; '.join([(e.prompt or '')[:160] for e in examples])
    prompt = (f'A single coherent realistic photographic scene of {visual_profile.partner_name}, a fictional adult age {visual_profile.fictional_age}. '
              f'Identity: {visual_profile.face_description}, {visual_profile.hair_description}, {visual_profile.eye_description}, {visual_profile.skin_description}, {visual_profile.body_description}, {visual_profile.distinguishing_details}. '
              f'Scene: {scene_type} at {current_location or location}; {lighting}; {camera}; {pose}; wardrobe: {wardrobe}. '
              f'Use plausible Iranian urban/home details without readable fake signage. No real person, no celebrity, no metadata, no text. ')
    if adult: prompt += 'Consensual fictional adult imagery only; all subjects are clearly 21+ fictional adults. '
    if example_note: prompt += f'Style reference from prior liked outputs summarized: {example_note}'
    return ImagePromptResult(prompt=prompt, negative_prompt=ADULT_NEGATIVE_PROMPT if adult else NORMAL_NEGATIVE_PROMPT, content_mode='adult' if adult else 'normal', scene_type=scene_type, location=current_location or location, camera=camera, lighting=lighting, pose=pose, wardrobe=wardrobe, continuity_notes='time/routine/city continuity applied', influenced_by_job_ids=[e.id for e in examples], input_context_summary=f'request={req[:120]}; mood={mood or getattr(user,"current_mood","")}; routine={routine_slot}')
