import json, struct, sys, threading, time, wave, os, urllib.request
REPO="/Users/chase/Documents/面试/meeting-copilot"
WAV=f"{REPO}/code/asr_runtime/outputs/simulated-release-review.16k.wav"
SID="real_e2e_real_llm"
PORT=8811
# load .env
for line in open(f"{REPO}/.env"):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,_,v=line.partition('='); os.environ[k.strip()]=v.strip().strip('"')

def wait_http(url,t=30):
    dl=time.time()+t
    while time.time()<dl:
        try:
            if urllib.request.urlopen(url,timeout=2).status==200: return True
        except: pass
        time.sleep(0.3)
    return False

def main():
    import subprocess, websocket
    env={"PATH":"/usr/bin:/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin",
         "PYTHONPATH":f"{REPO}/code/web_mvp/backend:{REPO}/code/core",
         "MEETING_COPILOT_DATA_DIR":"/tmp/mc-real-llm",
         "LLM_GATEWAY_BASE_URL":os.environ["LLM_GATEWAY_BASE_URL"],
         "LLM_GATEWAY_API_KEY":os.environ["LLM_GATEWAY_API_KEY"],
         "LLM_GATEWAY_MODEL":os.environ["LLM_GATEWAY_MODEL"]}
    uv=subprocess.Popen([sys.executable,"-m","uvicorn","meeting_copilot_web_mvp.app:app","--host","127.0.0.1","--port",str(PORT),"--log-level","warning"],
                        cwd=f"{REPO}/code/web_mvp/backend",env=env)
    try:
        if not wait_http(f"http://127.0.0.1:{PORT}/health"): print("FAIL: uvicorn"); return 1
        # 1. real wav -> WS -> sherpa sidecar -> session
        w=wave.open(WAV,"rb"); raw=w.readframes(w.getnframes())
        samples=struct.unpack("<"+str(len(raw)//2)+"h",raw)
        pcm=b"".join(struct.pack("<f",s/32768.0) for s in samples)
        ws=websocket.create_connection(f"ws://127.0.0.1:{PORT}/live/asr/stream/ws/{SID}",timeout=90)
        for i in range(0,len(pcm),19200): ws.send_binary(pcm[i:i+19200])
        ws.send("END"); ws.settimeout(90); events=[]
        while True:
            try:
                msg=ws.recv(); events.append(json.loads(msg))
                if json.loads(msg).get("event_type")=="final": break
                if len(events)>80: break
            except: break
        ws.close()
        finals=[e for e in events if e.get("event_type")=="final"]
        print(f"1) 真实 ASR (sherpa sidecar): {len(events)} 事件, final: {finals[-1].get('text','')[:100] if finals else 'NONE'}")
        # 2. session persisted
        r=urllib.request.urlopen(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/events",timeout=10)
        ed=json.loads(r.read()); print(f"2) session 持久化: {len(ed.get('events',[]))} 事件")
        # 3. REAL LLM cards
        req=urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/llm-execution-runs",data=json.dumps({"mode":"enabled"}).encode(),headers={"Content-Type":"application/json"})
        r=urllib.request.urlopen(req,timeout=60); body=json.loads(r.read())
        completed=[x for x in body["runs"] if x.get("card_status")=="new"]
        print(f"3) 真实 LLM 卡片: {len(completed)} 张 (run_count={body['run_count']})")
        for c in completed[:2]:
            card=c["card"]
            print(f"   - {card['suggestion_text'][:70]} | evidence={card.get('evidence_span_ids')} | tokens={card['llm_trace']['usage']['total_tokens']}")
        # 4. REAL minutes
        req=urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/minutes",data=json.dumps({"mode":"enabled"}).encode(),headers={"Content-Type":"application/json"})
        r=urllib.request.urlopen(req,timeout=60); m=json.loads(r.read())
        print(f"4) 真实纪要: {'OK '+(str(len(m['minutes_md']))+' chars') if m.get('minutes_md') else 'EMPTY'} (degraded={m.get('degraded')})")
        if m.get("minutes_md"): print("   纪要预览:", m["minutes_md"].replace(chr(10)," ")[:150])
        # 5. approach cards
        req=urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/approach-cards",data=json.dumps({"mode":"enabled"}).encode(),headers={"Content-Type":"application/json"})
        r=urllib.request.urlopen(req,timeout=60); ap=json.loads(r.read())
        print(f"5) 真实方案考量卡: {ap.get('count',0)} 张 (degraded={ap.get('degraded')})")
        ok = bool(completed) and m.get("minutes_md")
        print("\n完整真实链路:", "PASS ✓ (真实 ASR + 真实 LLM)" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        uv.kill()
sys.exit(main())
