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
    'image_request_verbs': (LexiconEntry('request_image', ('عکس','تصویر','بساز','بفرست','نشون'), category='route'),),
    'resend_phrases': (LexiconEntry('resend_exact', ('دوباره بفرست','همونو بفرست','قبلی رو بفرست'), category='continuity', priority=10),),
    'variation_phrases': (LexiconEntry('variation', ('یکی دیگه','یه دونه دیگه','مثل قبلی'), ('واریاسیون',), category='continuity', priority=20),),
    'refinement_phrases': (LexiconEntry('refinement', ('این بار','ولی','اصلاح کن','عوض کن','بهتر'), category='continuity', priority=30),),
    'negation': (LexiconEntry('negated', ('نه','نباشه','نمیخوام','بدون'), category='negation', suffix_stemming_allowed=False),),
    'scene_location': tuple(LexiconEntry(k, tuple(v), category='scene') for k,v in {'bedroom':['اتاق خواب'], 'bed':['تخت','رختخواب'], 'living_room':['پذیرایی','نشیمن'], 'sofa':['مبل','کاناپه'], 'bathroom':['حمام'], 'mirror':['آینه'], 'hotel_room':['هتل'], 'car':['ماشین','خودرو'], 'cafe':['کافه'], 'restaurant':['رستوران'], 'street':['خیابان'], 'park':['پارک'], 'beach':['ساحل'], 'office':['دفتر','اداره'], 'university':['دانشگاه'], 'metro':['مترو'], 'shop':['فروشگاه','مغازه'], 'gym':['باشگاه']}.items()),
    'support_surfaces': tuple(LexiconEntry(k, tuple(v), category='support_surface') for k,v in {'bed':['تخت'], 'sofa':['مبل','کاناپه'], 'chair':['صندلی'], 'floor':['زمین','کف'], 'car_seat':['صندلی ماشین'], 'standing':['ایستاده'], 'none':['هیچکدام']}.items()),
    'pose': tuple(LexiconEntry(k, tuple(v), category='pose') for k,v in {'reclining':['لم','تکیه'], 'lying':['دراز','خوابیده'], 'seated':['نشسته'], 'standing':['ایستاده'], 'walking':['راه','قدم']}.items()),
    'activity': (LexiconEntry('drinking_coffee', ('قهوه','می‌نوشم'), category='activity'), LexiconEntry('reading', ('کتاب','مطالعه'), category='activity')),
    'camera_framing': tuple(LexiconEntry(k, tuple(v), category='camera') for k,v in {'full_body':['تمام قد','فول بادی'], 'portrait':['پرتره','صورت'], 'selfie':['سلفی'], 'closeup':['کلوزآپ','نزدیک']}.items()),
    'wardrobe': tuple(LexiconEntry(k, tuple(v), category='wardrobe') for k,v in {'casual':['لباس راحتی','لباس خونه'], 'streetwear':['مانتو','کاپشن'], 'lingerie':['لباس زیر','لینجری']}.items()),
    'body_regions': tuple(LexiconEntry(k, tuple(v), category='body_region') for k,v in {'breasts':['سینه','پستان'], 'buttocks':['باسن'], 'genitals':['واژن','آلت','تناسلی'], 'upper_body':['بالا تنه'], 'lower_body':['پایین تنه'], 'full_body':['تمام بدن']}.items()),
    'body_visibility': (LexiconEntry('visible', ('معلوم','پیدا','دیده','نمایان'), category='body_visibility'),),
    'adult_intent': (LexiconEntry('adult_visual', ('لخت','برهنه','سکسی'), category='adult'),),
    'medical_nonvisual_context': (LexiconEntry('medical_discussion', ('درد','پزشکی','آناتومی','توضیح','در مورد'), category='nonvisual', negation_scope_applies=False),),
    'exclusions_corrections': (LexiconEntry('exclude', ('بدون','نباشه','حذف کن'), category='exclusion'),),
}
