from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class LexiconEntry:
    canonical: str
    persian_variants: tuple[str, ...]
    colloquial_variants: tuple[str, ...] = ()
    regex: str | None = None
    priority: int = 100
    category: str = ''
    suffix_stemming_allowed: bool = True
    negation_scope_applies: bool = True

IMAGE_SEMANTIC_LEXICONS: dict[str, tuple[LexiconEntry, ...]] = {
    'image_request_verbs': (LexiconEntry('request_image', ('عکس','تصویر','بساز','بفرست','نشون','ببینمت','بذار ببینمت','میخوام ببینمت','خودتو نشونم بده','نشونم بده'), category='route'),),
    'conversational_image_request_terms': (
        LexiconEntry('visibility_delivery_request', ('ببینمت','ببینم','بذار ببینمت','میخوام ببینمت','خودتو ببینم','نشونم بده','خودتو نشونم بده'), category='request_visibility', priority=40),
        LexiconEntry('harmless_filler', ('خب',), category='conversational_filler', priority=90, suffix_stemming_allowed=False),
    ),
    'resend_phrases': (LexiconEntry('resend_exact', ('دوباره بفرست','همونو بفرست','قبلی رو بفرست'), category='continuity', priority=10),),
    'variation_phrases': (LexiconEntry('variation', ('یکی دیگه','یه دونه دیگه','مثل قبلی'), ('واریاسیون',), category='continuity', priority=20),),
    'refinement_phrases': (LexiconEntry('refinement', ('این بار','ولی','اصلاح کن','عوض کن','بهتر'), category='continuity', priority=30),),
    'negation': (LexiconEntry('negated', ('نه','نباشه','نمیخوام','بدون'), category='negation', suffix_stemming_allowed=False),),
    'scene_location': tuple(LexiconEntry(k, tuple(v), category='scene') for k,v in {'bedroom':['اتاق خواب'], 'bed':['تخت','رختخواب'], 'living_room':['پذیرایی','نشیمن'], 'sofa':['مبل','کاناپه'], 'bathroom':['حمام'], 'mirror':['آینه'], 'hotel_room':['هتل'], 'car':['ماشین','خودرو'], 'cafe':['کافه'], 'restaurant':['رستوران'], 'street':['خیابان'], 'park':['پارک'], 'beach':['ساحل'], 'office':['دفتر','اداره'], 'university':['دانشگاه'], 'metro':['مترو'], 'shop':['فروشگاه','مغازه'], 'gym':['باشگاه']}.items()),
    'support_surfaces': tuple(LexiconEntry(k, tuple(v), category='support_surface') for k,v in {'bed':['تخت'], 'sofa':['مبل','کاناپه'], 'chair':['صندلی'], 'floor':['زمین','کف'], 'car_seat':['صندلی ماشین'], 'standing':['ایستاده'], 'none':['هیچکدام']}.items()),
    'pose': tuple(LexiconEntry(k, tuple(v), category='pose') for k,v in {'reclining':['لم','تکیه'], 'lying':['دراز','خوابیده'], 'seated':['نشسته'], 'standing':['ایستاده'], 'walking':['راه','قدم']}.items()),
    'activity': (LexiconEntry('drinking_coffee', ('قهوه','می‌نوشم'), category='activity'), LexiconEntry('reading', ('کتاب','مطالعه'), category='activity')),
    'interactions': (
        LexiconEntry('kiss', ('بوسیدن','بوسه','بوسیدن هم','در حال بوسیدن','لب گرفتن','همدیگه رو بوسیدن','همدیگر را بوسیدن'), category='interaction', priority=15),
        LexiconEntry('hug', ('بغل کردن','در آغوش گرفتن','همدیگه رو بغل کردن'), category='interaction', priority=15),
        LexiconEntry('holding_hands', ('دست همدیگه رو گرفتن','دست در دست'), category='interaction', priority=15),
    ),
    'secondary_subject_roles': (
        LexiconEntry('neighbor', ('همسایه','همسایه‌مون','همسایه مون','همسایه‌ام'), category='secondary_subject', priority=15),
        LexiconEntry('friend', ('دوست','دوستم'), category='secondary_subject', priority=30),
        LexiconEntry('partner', ('دوست پسر','دوست دختر','همسر','شوهر','زن'), category='secondary_subject', priority=15),
    ),
    'camera_framing': tuple(LexiconEntry(k, tuple(v), category='camera') for k,v in {'full_body':['تمام قد','تمام‌قد','فول بادی'], 'portrait':['پرتره','صورت'], 'selfie':['سلفی'], 'closeup':['کلوزآپ','نزدیک']}.items()),
    'wardrobe': tuple(LexiconEntry(k, tuple(v), category='wardrobe') for k,v in {'casual':['لباس راحتی','لباس خونه'], 'streetwear':['مانتو','کاپشن'], 'lingerie':['لباس زیر','لینجری','ست لباس زیر','بیکینی','لباس خواب جذاب']}.items()),
    'body_regions': tuple(LexiconEntry(k, tuple(v), category='body_region') for k,v in {'breasts':['سینه','سینه‌ها','پستان','ممه'], 'buttocks':['باسن','کون'], 'genitals':['واژن','آلت','تناسلی','کص','کس'], 'arms':['بازو','بازوها'], 'forearms':['ساعد'], 'hands':['دست'], 'lips':['لب','لبها','لب‌ها'], 'mouth':['دهان'], 'face':['صورت'], 'cheeks':['گونه'], 'eyes':['چشم'], 'hair':['مو','موها'], 'upper_body':['بالا تنه','بالاتنه'], 'lower_body':['پایین تنه'], 'full_body':['تمام بدن']}.items()),
    'expression_modifiers': tuple(LexiconEntry(k, tuple(v), category='expression_modifier') for k,v in {'pursed_lips':['قنچه','لب قنچه','لبات قنچه'], 'smile':['لبخند'], 'frown':['اخم'], 'eyes_closed':['چشم بسته'], 'eyes_open':['چشم باز'], 'hair_loose':['موهای باز'], 'hair_tied':['موهای بسته']}.items()),
    'body_visibility': (LexiconEntry('visible', ('معلوم','پیدا','دیده','نمایان'), category='body_visibility'),),
    'adult_intent': (
        LexiconEntry('suggestive', ('عکس بزرگسال','بزرگسالانه','۱۸+','18+','شیطون تر','شیطون‌تر','جذاب تر','جذاب‌تر','لباس بازتر'), category='adult'),
        LexiconEntry('topless', ('بالاتنه برهنه','تاپلس','سینه ها معلوم باشد','سینه‌ها معلوم باشد','بدون لباس بالاتنه'), category='adult'),
        LexiconEntry('full_nudity', ('کاملاً برهنه','کاملا برهنه','کاملاً لخت','کاملا لخت','بدون هیچ لباسی','برهنگی کامل','لخت','برهنه','بدون لباس'), category='adult'),
        LexiconEntry('unsupported_explicit_visibility', ('نمای نزدیک اندام تناسلی','نمایش صریح اندام تناسلی','genital close-up'), category='adult'),
    ),
    'medical_nonvisual_context': (LexiconEntry('medical_discussion', ('درد','پزشکی','آناتومی','توضیح','در مورد'), category='nonvisual', negation_scope_applies=False),),
    'exclusions_corrections': (LexiconEntry('exclude', ('بدون','نباشه','حذف کن'), category='exclusion'),),
}
