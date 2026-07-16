from __future__ import annotations
from dataclasses import dataclass, field
import re, unicodedata

_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_DIACRITICS = re.compile(r'[\u064B-\u065F\u0670\u0640]')
_PUNCT = str.maketrans({'،':',','؛':';','؟':'?','«':'"','»':'"','“':'"','”':'"','’':"'"})
SUFFIXES = sorted(['هامون','هاتون','هاشون','هام','هات','هاش','هایمو','هایتو','هاشو','هامو','هاتو','هامرو','هاترو','هاشرو','مون','تون','شون','مان','تان','شان','ام','ات','اش','مو','تو','شو','رو','را','ها','م','ت','ش','و'], key=len, reverse=True)

@dataclass(frozen=True)
class PersianToken:
    original: str
    normalized: str
    stem: str
    suffixes: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0

@dataclass(frozen=True)
class NormalizedPersianText:
    raw: str
    normalized: str
    tokens: list[PersianToken]


def normalize_chars(text: str) -> str:
    t = unicodedata.normalize('NFKC', text or '').translate(_DIGITS).translate(_PUNCT)
    t = t.replace('ي','ی').replace('ى','ی').replace('ك','ک').replace('ة','ه').replace('ۀ','ه').replace('ؤ','و').replace('إ','ا').replace('أ','ا').replace('ٱ','ا')
    t = _DIACRITICS.sub('', t).replace('\u200c', '‌')
    t = re.sub(r'\s*‌\s*', '‌', t)
    t = re.sub(r'\bمی\s+', 'می', t)
    return t.lower()


def _stem_token(tok: str) -> tuple[str, list[str]]:
    clean = tok.replace('‌','')
    suffixes: list[str] = []
    stem = clean
    object_marker = None
    if len(stem) > 2 and stem.endswith(('رو', 'را')):
        object_marker = stem[-2:]; stem = stem[:-2]
    elif len(stem) > 2 and stem.endswith('و'):
        object_marker = 'و'; stem = stem[:-1]
    possessive = None
    for suf in ('مون','تون','شون','مان','تان','شان','ام','ات','اش','م','ت','ش'):
        if len(stem) > len(suf) + 1 and stem.endswith(suf):
            possessive = suf; stem = stem[:-len(suf)]; break
    plural = None
    if len(stem) > 3 and stem.endswith('ها'):
        plural = 'ها'; stem = stem[:-2]
    if stem == 'موه' and possessive == 'ات':
        plural = 'ها'; possessive = 'ت'
    if stem == 'موه': stem = 'مو'
    if stem == 'سین': stem = 'سینه'
    if stem.endswith('ه') and possessive == 'ات':
        stem = stem[:-1]
        plural = 'ها'; possessive = 'ت'
    if stem == 'مم': stem = 'ممه'
    if plural: suffixes.append(plural)
    if possessive: suffixes.append(possessive)
    if object_marker: suffixes.append(object_marker)
    return stem, suffixes


def normalize_and_tokenize(text: str) -> NormalizedPersianText:
    raw = text or ''
    norm_chars = normalize_chars(raw)
    out_chars=[]; index_map=[]
    for i,ch in enumerate(norm_chars):
        if re.match(r'[\w\u0600-\u06FF‌]', ch):
            out_chars.append(ch); index_map.append(i)
        else:
            out_chars.append(' '); index_map.append(i)
    normalized = re.sub(r'\s+', ' ', ''.join(out_chars)).strip()
    tokens=[]
    # Find spans in normalized chars, map back to original offsets conservatively.
    for m in re.finditer(r'[^\s]+', ''.join(out_chars)):
        original = raw[index_map[m.start()]: index_map[m.end()-1]+1] if index_map else m.group(0)
        nt = m.group(0).replace('‌','')
        stem, suffixes = _stem_token(nt)
        tok=PersianToken(original=original, normalized=nt, stem=stem, suffixes=suffixes, start=index_map[m.start()], end=index_map[m.end()-1]+1)
        if tokens and nt in {'هاتو','هامو','هاشو','هات','هام','هاش','رو','را','و'}:
            prev=tokens[-1]
            _, extra=_stem_token(nt)
            if nt.startswith('ها') and 'ها' not in extra: extra.insert(0, 'ها')
            tokens[-1]=PersianToken(original=prev.original + ' ' + original, normalized=prev.normalized + nt, stem=prev.stem, suffixes=list(dict.fromkeys(list(prev.suffixes)+extra)), start=prev.start, end=tok.end)
        else:
            tokens.append(tok)
    return NormalizedPersianText(raw=raw, normalized=normalized, tokens=tokens)
