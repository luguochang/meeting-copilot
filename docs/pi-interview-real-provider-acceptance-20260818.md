# Pi Interview Real Provider Acceptance

Date: 2026-08-18

Branch: `feat/pi-realtime-coach-agent-loop`

Commit under test: `cfcc10e`

## Test Setup

- Meeting: `pi-interview-real-provider-20260818-002`
- Workbench: `http://127.0.0.1:8766/workbench?meeting_id=pi-interview-real-provider-20260818-002`
- Skill pack: `interview` / 用户访谈
- Input: direct stable transcript injection into the V2 transcript pipeline
- Audio: no playback, no speaker output, no microphone access
- Provider: `openai_compatible_gateway`
- Model: `gpt-5.6-sol`
- Provider probe: succeeded, `is_mock=false`

Injected transcript segments:

1. `system_audio:e1:interview-vague-1`: `Exporting data is always terrible and our team hates it. I cannot describe a recent example, the context, or what I tried; I only know the entire export workflow feels painful and should be redesigned.`
2. `system_audio:e1:interview-vague-2`: `Onboarding is confusing and everyone complains about it. I do not remember what happened the last time or which step blocked me; I only want the entire onboarding flow replaced.`

## Runtime Evidence

| Job | Event | Skill | Runtime | Fallback | Turns | Tools | Pi elapsed | Session |
| --- | --- | --- | --- | --- | ---: | --- | ---: | --- |
| `job_9a311c544b47390ef564baa6` | `discovery_gap` | `interview` | `pi` | `null` | 1 | `submit_intervention` | 6761.5 ms | new |
| `job_58f8c6687c6f8fa4bc0c3574` | `discovery_gap` | `interview` | `pi` | `null` | 1 | `submit_intervention` | 8759.9 ms | reused |

Both intelligence jobs succeeded with `llm_called=true`, `runtime_requested=pi`, `runtime_used=pi`, `fallback_error_code=null`, and `fallback_reason=null`. Both completed the base checklist plus the `discovery_depth` scene check. The second run reports `session_reused=true`.

The first recommendation asked for the most recent export episode. The second asked for the most recent onboarding episode. The page retained the first recommendation under `过去建议` while showing the second recommendation as the current card.

## Page Evidence

On the `会议重点` tab, the real Workbench showed:

- `用户访谈` scene skill badge
- `Pi 教练等待录音` runtime status：本次验收使用直接文字注入，没有启用麦克风；Pi 处理的是已注入的文字，不代表页面正在收音
- current `discovery_gap` recommendation and urgency
- intervention reason
- `查看依据` control and the verbatim transcript evidence
- one prior recommendation under `过去建议`

Screenshots are kept locally at:

- `artifacts/pi-real-provider-acceptance/2026-08-18/pi-interview-discovery-gap-history.png`
- `artifacts/pi-real-provider-acceptance/2026-08-18/pi-interview-discovery-gap-coach-tall.png`
- `artifacts/pi-real-provider-acceptance/2026-08-18/pi-interview-discovery-gap-current-default.png`

## Result

The real-provider and Pi integration gate passed: the page result is a new `discovery_gap` intervention produced by Pi with no direct-LLM fallback, and the page exposes its reason, evidence, and retained history.

The follow-up status fix is recorded in commit `c56dfdc`: a live meeting without a recording session or durable audio chunk now reports `等待录音`, and the Pi badge reports `Pi 教练等待录音`. This prevents an injected-text acceptance fixture from appearing to listen to the microphone.

The latency target did not pass in this run. Pi coach elapsed time was 6.76 s and 8.76 s; the intelligence provider-total sample was 10.61 s. This is an observed performance gap, not a mocked or suppressed result, and should be addressed separately before claiming a 3.5 s P95 target.
