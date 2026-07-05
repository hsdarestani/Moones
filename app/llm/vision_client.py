from __future__ import annotations
import base64, json, re
import httpx
from app.core.config import get_settings
from app.llm.client import extract_text_from_venice_response

VISION_PROMPT = '''You are a visual perception module for a Persian AI companion.
Analyze the image carefully and return JSON only.
Do not identify the person. Do not infer sensitive attributes such as exact age, ethnicity, religion, health, wealth, or exact location. Do not sexualize the person. If the image may contain a minor, keep compliments non-romantic and non-sexual. If the image is unclear, say confidence is low.
Return: {"image_type":"selfie | portrait | group | object | place | screenshot | unclear","has_person":true,"may_contain_minor":false,"visible_details":[],"mood":"","style":"","safe_compliment_angles":[],"things_to_ask_about":[],"caption_context":"","confidence":"low | medium | high"}'''

def _json(text: str) -> dict:
    m=re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0) if m else text)

async def analyze_image_with_venice(image_path: str, *, user_caption: str | None = None, model: str | None = None) -> dict:
    settings=get_settings(); model=model or settings.vision_model
    data=base64.b64encode(open(image_path,'rb').read()).decode('ascii')
    payload={"model":model,"messages":[{"role":"user","content":[{"type":"text","text":VISION_PROMPT + (f"\nUser caption: {user_caption}" if user_caption else "")},{"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{data}"}}]}],"temperature":0.1,"max_tokens":700}
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.post(f"{settings.venice_api_base_url.rstrip('/')}/chat/completions", headers={"Authorization":f"Bearer {settings.venice_api_key}","Content-Type":"application/json"}, json=payload)
    if r.status_code>=400: raise RuntimeError(r.text[:500])
    text,_=extract_text_from_venice_response(r.json())
    out=_json(text); out["model"]=model; return out
