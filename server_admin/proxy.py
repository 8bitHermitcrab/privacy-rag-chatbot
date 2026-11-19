"""로컬 검증용 가중 분산기. 클라우드 운영용 분산기를 대체하지 않습니다."""
import hmac
import threading
import time

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .operations import NODES, PROXY_PORT, settings, healthy

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
LOCK=threading.RLock()
COUNTS={node:{"active":0,"requests":0,"errors":0,"healthy":False} for node in NODES}
CURRENT={node:0 for node in NODES}
DRAINED=set()
HOP={"connection","keep-alive","proxy-authenticate","proxy-authorization","te","trailer","transfer-encoding","upgrade","host","content-length"}

def choose(nodes,weights):
    """가중치 합에 비례하는 smooth weighted round robin."""
    if not nodes:return None
    with LOCK:
        for node in NODES:
            if node not in nodes:CURRENT[node]=0
        for node in nodes:CURRENT[node]+=weights[node]
        selected=max(nodes,key=lambda n:CURRENT[n])
        CURRENT[selected]-=sum(weights[n] for n in nodes)
        return selected

@app.get('/__manager/status')
def status(request:Request):
    if not hmac.compare_digest(request.headers.get('x-manager-token',''),settings()['proxy_token']):
        return JSONResponse({'detail':'요청을 처리할 수 없습니다.'},status_code=404)
    with LOCK:return {'kind':'rag-local-proxy','nodes':{k:dict(v) for k,v in COUNTS.items()}}

@app.post('/__manager/route')
async def route(request:Request):
    if not hmac.compare_digest(request.headers.get('x-manager-token',''),settings()['proxy_token']):
        return JSONResponse({'detail':'요청을 처리할 수 없습니다.'},status_code=404)
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>1024:return JSONResponse({'detail':'요청 제한 초과'},status_code=413)
    try:
        import json
        value=json.loads(data)
        if value['node'] not in NODES or type(value['enabled']) is not bool:raise ValueError()
    except (ValueError,KeyError,TypeError):
        return JSONResponse({'detail':'허용되지 않은 요청'},status_code=400)
    with LOCK:
        if value['enabled']:DRAINED.discard(value['node'])
        else:DRAINED.add(value['node'])
        return {'ok':True,'active':COUNTS[value['node']]['active']}

def forward(method,path,query,body,headers):
    config=settings()
    online={node:healthy(node) for node in NODES}
    with LOCK:
        for node in NODES:COUNTS[node]['healthy']=online[node]
    candidates=[n for n in NODES if online[n] and config['nodes'][n]['enabled'] and config['nodes'][n]['weight']>0]
    weights={n:config['nodes'][n]['weight'] for n in NODES}
    while candidates:
        node=choose(candidates,weights)
        # 분산 제외와 선택 사이 경합을 줄이기 위해 실제 전달 직전 설정을 다시 확인합니다.
        with LOCK:
            current=settings()['nodes'][node]
            if node in DRAINED or not current['enabled'] or current['weight']==0:
                candidates.remove(node);continue
            COUNTS[node]['active']+=1;COUNTS[node]['requests']+=1
        try:
            with requests.Session() as client:
                client.trust_env=False
                result=client.request(method,f'http://127.0.0.1:{NODES[node]}/'+path+('?' + query if query else ''),
                    data=body,headers=headers,timeout=(2,180),allow_redirects=False,stream=True)
                with result:
                    value=bytearray()
                    for chunk in result.iter_content(65536):
                        value.extend(chunk)
                        if len(value)>16*1024*1024:
                            return JSONResponse({'detail':'응답 크기 제한을 초과했습니다.'},status_code=502)
                    extra=set(x.strip().lower() for x in result.headers.get('connection','').split(','))
                    response_headers={k:v for k,v in result.headers.items() if k.lower() not in HOP|extra|{'content-encoding','set-cookie'}}
                    response_headers['X-RAG-Backend']=node
                    response=Response(bytes(value),status_code=result.status_code,headers=response_headers)
                    # 업무 로그인 쿠키를 분산기에서도 전달합니다. 서버 관리자 쿠키는 전달하지 않습니다.
                    for cookie in result.raw.headers.getlist('Set-Cookie'):
                        if cookie.split('=',1)[0].strip() == 'rag_business':
                            response.headers.append('set-cookie',cookie)
                    return response
        except requests.RequestException:
            with LOCK:COUNTS[node]['errors']+=1;COUNTS[node]['healthy']=False
            candidates.remove(node)
            # POST를 자동 재전송하면 LLM 호출·등록이 중복될 수 있어 GET/HEAD만 우회합니다.
            if method not in ('GET','HEAD'):break
        finally:
            with LOCK:COUNTS[node]['active']-=1
    return JSONResponse({'detail':'요청을 처리할 정상 서버가 없습니다.'},status_code=503)

@app.api_route('/{path:path}',methods=['GET','POST','HEAD','OPTIONS'])
async def proxy(path:str,request:Request):
    if path.startswith('__manager'):
        return JSONResponse({'detail':'허용되지 않은 요청입니다.'},status_code=404)
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>2*1024*1024:
            return JSONResponse({'detail':'요청 크기 제한을 초과했습니다.'},status_code=413)
    extra=set(x.strip().lower() for x in request.headers.get('connection','').split(','))
    headers={k:v for k,v in request.headers.items() if k.lower() not in HOP|extra|{'x-manager-token','x-forwarded-for','x-forwarded-host','x-forwarded-proto'}}
    if 'cookie' in headers:
        headers['cookie']='; '.join(pair.strip() for pair in headers['cookie'].split(';') if pair.strip().split('=',1)[0]!='rag_server_admin')
    return await run_in_threadpool(forward,request.method,path,request.url.query,bytes(body),headers)

if __name__=='__main__':
    uvicorn.run(app,host='127.0.0.1',port=PROXY_PORT,proxy_headers=False,access_log=False,workers=1)
