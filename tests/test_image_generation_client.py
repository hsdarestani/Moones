import base64, asyncio, httpx, pytest
from app.llm.image_client import VeniceImageClient, venice_image_payload, image_resolution_tier, ImageValidationError, ImageBadResponse

def test_payload_defaults_and_binary_response():
    async def run():
        seen={}
        def handler(req):
            seen['auth']=req.headers['authorization']; seen['payload']=__import__('json').loads(req.content)
            return httpx.Response(200, headers={'content-type':'image/png','x-request-id':'r1'}, content=b'\x89PNGabc')
        client=VeniceImageClient(api_key='secret', client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://api.venice.ai'), max_attempts=1)
        res=await client.generate('p','n')
        assert seen['auth']=='Bearer secret'
        assert seen['payload']==venice_image_payload('p','n')
        assert seen['payload']['safe_mode'] is False and res.response_type=='binary'
    asyncio.run(run())

def test_json_base64_and_invalid_mime():
    async def run():
        img=base64.b64encode(b'imgbytes').decode()
        client=VeniceImageClient(api_key='s', client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'image_base64':img,'mime_type':'image/jpeg'})), base_url='https://api.venice.ai'), max_attempts=1)
        assert (await client.generate('p','n')).image_bytes == b'imgbytes'
        bad=VeniceImageClient(api_key='s', client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={'content-type':'text/html'}, content=b'<html>'))), max_attempts=1)
        with pytest.raises(ImageBadResponse): await bad.generate('p','n')
    asyncio.run(run())

def test_400_not_retried_503_retried():
    async def run():
        calls={'n':0}
        bad=VeniceImageClient(api_key='s', client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400))), max_attempts=3)
        with pytest.raises(ImageValidationError): await bad.generate('p','n')
        def handler(r):
            calls['n']+=1
            return httpx.Response(503) if calls['n']==1 else httpx.Response(200, headers={'content-type':'image/png'}, content=b'png')
        ok=VeniceImageClient(api_key='s', client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_attempts=2)
        await ok.generate('p','n')
        assert calls['n']==2
    asyncio.run(run())

def test_resolution_tier():
    assert image_resolution_tier(1024,1280)=='image_1k'
    assert image_resolution_tier(2048,2048)=='image_2k'
