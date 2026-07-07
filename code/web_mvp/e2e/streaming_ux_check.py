import os, sys, json, time, struct, wave, threading, urllib.request
import websocket
REPO="/Users/chase/Documents/面试/meeting-copilot"
WAV=f"{REPO}/data/asr_eval/local_samples/long_meeting.16k.wav"
PORT=8813
import subprocess
env={"PATH":"/usr/bin:/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin","PYTHONPATH":f"{REPO}/code/web_mvp/backend:{REPO}/code/core","MEETING_COPILOT_DATA_DIR":"/tmp/mc-stream"}
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
    if not wait(f"http://127.0.0.1:{PORT}/health"): print("FAIL uvicorn"); sys.exit(1)
    w=wave.open(WAV,"rb"); raw=w.readframes(w.getnframes())
    samples=struct.unpack("<"+str(len(raw)//2)+"h",raw)
    pcm=b"".join(struct.pack("<f",s/32768.0) for s in samples)
    ws=websocket.create_connection(f"ws://127.0.0.1:{PORT}/live/asr/stream/ws/stream_ux",timeout=120)
    t0=time.time()
    # stream in ~0.3s chunks (4800 samples), but burst (no real-time sleep) to test processing
    chunk=4800*4; events=[]; first_partial_t=None
    def reader():
        global first_partial_t
        ws.settimeout(120)
        while True:
            try:
                msg=ws.recv(); ev=json.loads(msg); ev["_t"]=time.time()-t0; events.append(ev)
                if ev.get("event_type")=="partial" and first_partial_t is None:
                    first_partial_t=ev["_t"]
                if ev.get("event_type")=="final": break
            except: break
    rt=threading.Thread(target=reader,daemon=True); rt.start()
    for i in range(0,len(pcm),chunk):
        ws.send_binary(pcm[i:i+chunk])
    ws.send("END")
    rt.join(timeout=90)
    partials=[e for e in events if e.get("event_type")=="partial"]
    finals=[e for e in events if e.get("event_type")=="final"]
    print(f"流式效果（{len(pcm)//4} samples, {len(pcm)//4/16000:.1f}s 音频）:")
    print(f"  总事件: {len(events)} (partial {len(partials)}, final {len(finals)})")
    print(f"  首个 partial 到达: {first_partial_t:.2f}s" if first_partial_t else "  无 partial")
    if len(partials)>=3:
        gaps=[partials[i+1]["_t"]-partials[i]["_t"] for i in range(min(5,len(partials)-1))]
        print(f"  partial 间隔(前5): {[f'{g:.2f}s' for g in gaps]}")
    if finals: print(f"  final 到达: {finals[0]['_t']:.2f}s | 文本: {finals[0].get('text','')[:100]}")
    # 流式效果判定：partial 增量到达（>=3 个且首个 < 5s）
    ok = len(partials)>=3 and first_partial_t<5
    print(f"\n前端流式效果: {'PASS ✓ (增量 partial)' if ok else 'FAIL (非增量或无 partial)'}")
finally:
    uv.kill()
