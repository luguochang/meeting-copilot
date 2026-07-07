import os, sys, json, time, urllib.request, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
REPO="/Users/chase/Documents/面试/meeting-copilot"
WAV=f"{REPO}/code/asr_runtime/outputs/simulated-release-review.16k.wav"
PORT=8812
for line in open(f"{REPO}/.env"):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,_,v=line.partition('='); os.environ[k.strip()]=v.strip().strip('"')
def wait_http(u,t=30):
    dl=time.time()+t
    while time.time()<dl:
        try:
            if urllib.request.urlopen(u,timeout=2).status==200: return True
        except: pass
        time.sleep(0.3)
    return False
env={"PATH":"/usr/bin:/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin","PYTHONPATH":f"{REPO}/code/web_mvp/backend:{REPO}/code/core","MEETING_COPILOT_DATA_DIR":"/tmp/mc-file","LLM_GATEWAY_BASE_URL":os.environ["LLM_GATEWAY_BASE_URL"],"LLM_GATEWAY_API_KEY":os.environ["LLM_GATEWAY_API_KEY"],"LLM_GATEWAY_MODEL":os.environ["LLM_GATEWAY_MODEL"]}
uv=subprocess.Popen([sys.executable,"-m","uvicorn","meeting_copilot_web_mvp.app:app","--host","127.0.0.1","--port",str(PORT),"--log-level","warning"],cwd=f"{REPO}/code/web_mvp/backend",env=env)
try:
    if not wait_http(f"http://127.0.0.1:{PORT}/health"): print("FAIL uvicorn"); sys.exit(1)
    # 1. upload wav -> transcribe-file -> session
    boundary="----mc"; body=open(WAV,"rb").read()
    req=urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/transcribe-file/sessions",
        data=b"--"+boundary.encode()+b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"meeting.wav\"\r\nContent-Type: audio/wav\r\n\r\n"+body+b"\r\n--"+boundary.encode()+b"--\r\n",
        headers={"Content-Type":f"multipart/form-data; boundary={boundary}"})
    r=urllib.request.urlopen(req,timeout=200); s=json.loads(r.read())
    print(f"1) 文件转换: session={s['session_id']}, transcript={s['transcript'][:80]}...")
    # 2. real LLM cards
    req=urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{s['session_id']}/llm-execution-runs",data=json.dumps({"mode":"enabled"}).encode(),headers={"Content-Type":"application/json"})
    r=urllib.request.urlopen(req,timeout=60); body=json.loads(r.read())
    completed=[x for x in body["runs"] if x.get("card_status")=="new"]
    print(f"2) 真实 LLM 卡片: {len(completed)} 张")
    for c in completed[:1]: print(f"   - {c['card']['suggestion_text'][:70]}")
    # 3. minutes
    req=urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{s['session_id']}/minutes",data=json.dumps({"mode":"enabled"}).encode(),headers={"Content-Type":"application/json"})
    r=urllib.request.urlopen(req,timeout=60); m=json.loads(r.read())
    print(f"3) 纪要: {'OK' if m.get('minutes_md') else 'EMPTY'}")
    ok=bool(completed) and m.get("minutes_md")
    print("\n录音文件转换链路:", "PASS ✓ (上传→FunASR batch→真实 LLM)" if ok else "FAIL")
finally:
    uv.kill()
