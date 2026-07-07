import os, sys, json, time, struct, wave, subprocess, urllib.request
import websocket
REPO="/Users/chase/Documents/面试/meeting-copilot"
WAV=f"{REPO}/data/asr_eval/local_samples/long_meeting.16k.wav"
PORT=8814
for line in open(f"{REPO}/.env"):
    line=line.strip()
    if "=" in line and not line.startswith("#"):
        k,_,v=line.partition("="); os.environ[k.strip()]=v.strip().strip('"')
env={"PATH":"/usr/bin:/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin","PYTHONPATH":f"{REPO}/code/web_mvp/backend:{REPO}/code/core","MEETING_COPILOT_DATA_DIR":"/tmp/mc-rtl2","LLM_GATEWAY_BASE_URL":os.environ["LLM_GATEWAY_BASE_URL"],"LLM_GATEWAY_API_KEY":os.environ["LLM_GATEWAY_API_KEY"],"LLM_GATEWAY_MODEL":os.environ["LLM_GATEWAY_MODEL"]}
uv=subprocess.Popen([sys.executable,"-m","uvicorn","meeting_copilot_web_mvp.app:app","--host","127.0.0.1","--port",str(PORT),"--log-level","warning"],cwd=f"{REPO}/code/web_mvp/backend",env=env)
def wait(u,t=30):
    dl=time.time()+t
    while time.time()<dl:
        try:
            if urllib.request.urlopen(u,timeout=2).status==200: return True
        except: pass
        time.sleep(0.3)
    return False
try:
    if not wait(f"http://127.0.0.1:{PORT}/health"): print("FAIL"); sys.exit(1)
    SID="rtl2_"+str(__import__("time").time())[-5:]
    w=wave.open(WAV,"rb"); raw=w.readframes(w.getnframes()); samples=struct.unpack("<"+str(len(raw)//2)+"h",raw)
    pcm=b"".join(struct.pack("<f",s/32768.0) for s in samples)
    ws=websocket.create_connection(f"ws://127.0.0.1:{PORT}/live/asr/stream/ws/{SID}",timeout=120)
    t0=time.time()
    for i in range(0,len(pcm),19200): ws.send_binary(pcm[i:i+19200])
    ws.send("END"); ws.settimeout(120)
    while True:
        try:
            msg=ws.recv()
            if json.loads(msg).get("event_type")=="final": break
        except: break
    ws.close(); t1=time.time()
    print(f"WS 流式+L2 完成: {t1-t0:.1f}s")
    # GET session transcript (L2-corrected)
    r=urllib.request.urlopen(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/events",timeout=10)
    events=json.loads(r.read())["events"]
    finals=[e for e in events if e["event_type"]=="transcript_final"]
    if finals:
        text=finals[-1]["payload"].get("normalized_text") or finals[-1]["payload"].get("text","")
        print(f"session transcript_final (L2 修正后): {text[:250]}...")
        ENTITIES=["支付服务","灰度","P99","错误率","回滚","v2.3","盯盘"]
        found=[e for e in ENTITIES if e.lower() in text.lower()]
        print(f"实体 recall: {len(found)}/{len(ENTITIES)} = {len(found)/len(ENTITIES):.2f} found={found}")
    ok = bool(finals) and "灰度" in (text if finals else "")
    print("\n实时 sherpa+L2 端到端:", "PASS ✓" if ok else "FAIL")
finally:
    uv.kill()
