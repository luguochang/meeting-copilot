# Realtime coach A/B replay

This tool compares the direct structured-LLM coach with the optional Pi agent runtime on the same source-aware transcript cases and the same configured model.

It does not capture or play audio. `system_audio` in a fixture represents the event emitted by the existing WASAPI loopback/ASR path, so the AI decision layer can be evaluated without audible speaker playback.

## Run

Configure the same OpenAI-compatible gateway used by Talktrace:

```powershell
$env:LLM_GATEWAY_BASE_URL = "https://gateway.example"
$env:LLM_GATEWAY_API_KEY = "..."
$env:LLM_GATEWAY_MODEL = "..."

cd code/web_mvp/backend
uv run --frozen python ../../../tools/realtime_coach_eval/replay.py ../../../tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl --runtime both --output ../../../artifacts/realtime-coach-eval.json
```

For Pi, install the pinned bridge dependencies once:

```powershell
cd code/agent_runtime/pi_coach_bridge
npm ci --ignore-scripts --no-audit --no-fund
```

The report includes intervention precision/recall, silent accuracy, required-evidence accuracy, deadline pass rate, P50/P95 latency, Agent turns, Pi fallback count, and provider errors. A faux-provider smoke proves SDK wiring only; it is not product-quality evidence.
