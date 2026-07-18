from __future__ import annotations
import logging
import asyncio, base64, random, time
from dataclasses import dataclass
from email.message import Message
from urllib.parse import urljoin
import httpx
from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL='krea-2-turbo'; DEFAULT_WIDTH=1024; DEFAULT_HEIGHT=1280; DEFAULT_STEPS=45; DEFAULT_CFG_SCALE=4; VENICE_SEED_MIN=1; VENICE_SEED_MAX=999_999_999; DEFAULT_SEED=VENICE_SEED_MIN; MAX_PROVIDER_IMAGE_BYTES=12_000_000
SUPPORTED_IMAGE_DIMENSIONS={(1024,1280),(1280,1024)}

@dataclass
class ImageGenerationResponse:
    image_bytes: bytes; mime_type: str; request_id: str|None; model: str; width: int; height: int; latency_seconds: float; response_type: str; metadata: dict
class ImageClientError(Exception): retryable=False; code='image_error'
class ImageValidationError(ImageClientError): code='validation'
class ImageAuthError(ImageClientError): code='auth'
class ImageBalanceError(ImageClientError): code='balance'
class ImageRateLimitError(ImageClientError): retryable=True; code='rate_limited'
class ImageProviderUnavailable(ImageClientError): retryable=True; code='provider_unavailable'
class ImageBadResponse(ImageClientError): code='bad_response'


def _safe_provider_detail(
    resp: httpx.Response,
) -> str:
    try:
        detail = ' '.join(
            (resp.text or '').split()
        )
    except Exception:
        detail = 'unreadable_response'

    return (
        detail
        or 'empty_response'
    )[:500]


def image_resolution_tier(width:int, height:int)->str:
    return 'image_1k' if width*height <= 1024*1280 else 'image_2k'

def validate_image_dimensions(width:int, height:int, *, model:str=DEFAULT_IMAGE_MODEL)->tuple[int,int]:
    if (int(width), int(height)) not in SUPPORTED_IMAGE_DIMENSIONS:
        raise ImageValidationError(f'unsupported_dimensions:{width}x{height}')
    return int(width), int(height)

def normalize_venice_seed(seed:int|str|None, *, salt:str='')->tuple[int,bool]:
    requested = DEFAULT_SEED if seed is None else int(seed)
    if VENICE_SEED_MIN <= requested <= VENICE_SEED_MAX:
        return requested, False
    digest=int(__import__('hashlib').sha256(f'{requested}:{salt}'.encode()).hexdigest(),16)
    return VENICE_SEED_MIN + (digest % VENICE_SEED_MAX), True

def venice_image_payload(prompt:str, negative_prompt:str, *, width:int=DEFAULT_WIDTH, height:int=DEFAULT_HEIGHT, model:str=DEFAULT_IMAGE_MODEL, seed:int=DEFAULT_SEED)->dict:
    width, height = validate_image_dimensions(width, height, model=model)
    provider_seed,_ = normalize_venice_seed(seed, salt=f'{model}:{width}x{height}')
    return {'model':model,'prompt':prompt,'negative_prompt':negative_prompt,'safe_mode':False,'width':width,'height':height,'steps':DEFAULT_STEPS,'cfg_scale':DEFAULT_CFG_SCALE,'seed':provider_seed,'return_binary':True}

def _endpoint(base: str) -> str:
    base=(base or 'https://api.venice.ai/api/v1').rstrip('/') + '/'
    if base.endswith('/api/v1/'): return urljoin(base, 'image/generate')
    return 'https://api.venice.ai/api/v1/image/generate'

def _extract_json_image(data: dict) -> tuple[bytes,str]:
    val = data.get('image') or data.get('image_base64') or data.get('data') or ((data.get('images') or [{}])[0].get('b64_json') if isinstance(data.get('images'), list) else None)
    if not val: raise ImageBadResponse('missing_image')
    if isinstance(val, str) and val.startswith('data:'):
        header, val = val.split(',',1); mime=header.split(';')[0].replace('data:','') or 'image/png'
    else: mime=data.get('mime_type') or 'image/png'
    return base64.b64decode(val), mime

def _validate(content: bytes, mime: str) -> None:
    if not mime.startswith('image/'): raise ImageBadResponse('invalid_mime')
    if mime in {'text/html','application/json'}: raise ImageBadResponse('error_body')
    if not content or len(content)>MAX_PROVIDER_IMAGE_BYTES: raise ImageBadResponse('invalid_size')
    if content[:15].lower().startswith(b'<!doctype html') or content[:6].lower().startswith(b'<html>'): raise ImageBadResponse('html_body')

class VeniceImageClient:
    def __init__(self, api_key: str|None=None, base_url: str|None=None, client: httpx.AsyncClient|None=None, max_attempts:int=3):
        s=get_settings(); self.api_key=api_key if api_key is not None else s.venice_api_key; self.base_url=base_url or s.venice_api_base_url; self.client=client; self.max_attempts=max_attempts
    async def generate(self, prompt:str, negative_prompt:str, *, width:int=DEFAULT_WIDTH, height:int=DEFAULT_HEIGHT, seed:int=DEFAULT_SEED, model:str=DEFAULT_IMAGE_MODEL) -> ImageGenerationResponse:
        if not self.api_key: raise ImageAuthError('missing_api_key')
        payload=venice_image_payload(prompt, negative_prompt, width=width, height=height, seed=seed, model=model); headers={'Authorization':f'Bearer {self.api_key}','Content-Type':'application/json'}; url=_endpoint(self.base_url)
        timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10)
        last=None
        seed_fallback_used=False
        for attempt in range(1,self.max_attempts+1):
            started=time.monotonic()
            try:
                if self.client: resp=await self.client.post(url, json=payload, headers=headers)
                else:
                    async with httpx.AsyncClient(timeout=timeout) as c: resp=await c.post(url, json=payload, headers=headers)
                if resp.status_code in (400,415):
                    detail = _safe_provider_detail(
                        resp
                    )

                    if (
                        resp.status_code == 400
                        and payload.get('seed')
                        != DEFAULT_SEED
                        and not seed_fallback_used
                    ):
                        logger.warning(
                            'IMAGE_PROVIDER_SEED_FALLBACK '
                            'status=%s detail=%s',
                            resp.status_code,
                            detail,
                        )

                        payload = {
                            **payload,
                            'seed': DEFAULT_SEED,
                        }

                        seed_fallback_used = True
                        continue

                    raise ImageValidationError(
                        f'{resp.status_code}:'
                        f'{detail}'
                    )
                if resp.status_code==401: raise ImageAuthError('401')
                if resp.status_code==402: raise ImageBalanceError('402')
                if resp.status_code==429: raise ImageRateLimitError('429')
                if resp.status_code in (500,503): raise ImageProviderUnavailable(str(resp.status_code))
                if resp.status_code>=400: raise ImageClientError(str(resp.status_code))
                ctype=(resp.headers.get('content-type') or '').split(';')[0].lower()
                if ctype.startswith('image/'):
                    img=resp.content; mime=ctype; rtype='binary'
                elif ctype=='application/json':
                    img,mime=_extract_json_image(resp.json()); rtype='json_base64'
                else: raise ImageBadResponse('invalid_mime')
                _validate(img,mime)
                return ImageGenerationResponse(
                    img,
                    mime,
                    (
                        resp.headers.get(
                            'x-request-id'
                        )
                        or resp.headers.get(
                            'request-id'
                        )
                    ),
                    model,
                    width,
                    height,
                    time.monotonic()-started,
                    rtype,
                    {
                        'seed_used': (
                            payload.get('seed')
                        ),
                        'seed_fallback_used': (
                            seed_fallback_used
                        ),
                    },
                )
            except (httpx.TimeoutException, ImageRateLimitError, ImageProviderUnavailable) as exc:
                last=exc
                if attempt>=self.max_attempts: raise ImageProviderUnavailable(str(exc))
                retry_after = None
                if 'resp' in locals(): retry_after=resp.headers.get('Retry-After')
                delay=float(retry_after) if retry_after and retry_after.isdigit() else (0.25*(2**(attempt-1))+random.random()*0.1)
                await asyncio.sleep(delay)
        raise ImageProviderUnavailable(str(last))
