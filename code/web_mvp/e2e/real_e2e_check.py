"""Real end-to-end verification: real wav -> mic_capture -> WS -> sherpa sidecar
-> session -> real LLM (local mock) -> real cards.

Proves the full connected product pipeline with REAL ASR (sherpa) + real httpx LLM.
"""
import json
import struct
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request

REPO = "/Users/chase/Documents/面试/meeting-copilot"
WAV = f"{REPO}/code/asr_runtime/outputs/simulated-release-review.16k.wav"
SID = "real_e2e_sherpa"
PORT = 8810


def start_mock_openai():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            content = json.dumps({"suggestion_text": "建议确认 rollback 负责人", "confidence": 0.85, "trigger_reason": "owner 缺失"}, ensure_ascii=False)
            resp = {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
            data = json.dumps(resp).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *a): pass
    s = ThreadingHTTPServer(("127.0.0.1", 0), H); t = threading.Thread(target=s.serve_forever, daemon=True); t.start()
    return s, s.server_address[1]


def wait_http(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200: return True
        except: pass
        time.sleep(0.2)
    return False


def main():
    import subprocess
    mock_srv, mock_port = start_mock_openai()
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin",
           "PYTHONPATH": f"{REPO}/code/web_mvp/backend:{REPO}/code/core",
           "MEETING_COPILOT_DATA_DIR": "/tmp/mc-real-e2e",
           "LLM_GATEWAY_BASE_URL": f"http://127.0.0.1:{mock_port}",
           "LLM_GATEWAY_API_KEY": "sk-test", "LLM_GATEWAY_MODEL": "mock"}
    uv = subprocess.Popen([sys.executable, "-m", "uvicorn", "meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
                          cwd=f"{REPO}/code/web_mvp/backend", env=env)
    try:
        if not wait_http(f"http://127.0.0.1:{PORT}/health"):
            print("FAILED: uvicorn not up"); return 1
        # 1. stream real wav via WS -> sherpa sidecar -> session
        import websocket
        w = wave.open(WAV, "rb"); raw = w.readframes(w.getnframes())
        samples = struct.unpack("<" + str(len(raw) // 2) + "h", raw)
        pcm = b"".join(struct.pack("<f", s / 32768.0) for s in samples)
        ws = websocket.create_connection(f"ws://127.0.0.1:{PORT}/live/asr/stream/ws/{SID}", timeout=60)
        events = []
        for i in range(0, len(pcm), 19200):
            ws.send_binary(pcm[i:i+19200])
        ws.send("END")
        ws.settimeout(60)
        while True:
            try:
                msg = ws.recv(); events.append(json.loads(msg))
                if json.loads(msg).get("event_type") == "final": break
                if len(events) > 80: break
            except: break
        ws.close()
        finals = [e for e in events if e.get("event_type") == "final"]
        print(f"WS ASR events: {len(events)} ({len(finals)} finals)")
        if finals: print("  real sherpa final:", finals[-1].get("text", "")[:120])

        # 2. session persisted?
        r = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/events", timeout=10)
        ev_data = json.loads(r.read())
        print(f"  persisted events: {len(ev_data.get('events', []))}")
        assert ev_data.get("events"), "session not persisted from real WS stream"

        # 3. real LLM cards from real ASR session
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/llm-execution-runs",
            data=json.dumps({"mode": "enabled"}).encode(),
            headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=30)
        body = json.loads(r.read())
        print(f"  llm-execution-runs: {body['run_count']} runs")
        completed = [x for x in body["runs"] if x.get("card_status") == "new"]
        print(f"  real cards: {len(completed)}")
        if completed:
            c = completed[0]["card"]
            print(f"    card: {c['suggestion_text'][:60]} | evidence: {c.get('evidence_span_ids')} | tokens: {c['llm_trace']['usage']['total_tokens']}")
        # 4. minutes
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/live/asr/sessions/{SID}/minutes",
            data=json.dumps({"mode": "enabled"}).encode(), headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=30)
        m = json.loads(r.read())
        print(f"  minutes: {'OK' if m.get('minutes_md') else 'EMPTY'} (degraded={m.get('degraded')})")
        ok = bool(completed) and m.get("minutes_md")
        print("\nREAL E2E:", "PASS ✓" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        uv.kill()
        mock_srv.shutdown()


sys.exit(main())
