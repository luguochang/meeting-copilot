# PI Agent Real Long-Audio Acceptance Addendum

Date: 2026-08-19

## Browser evidence

The real Chrome acceptance page was loaded at:

`http://127.0.0.1:8767/workbench?meeting_id=pi-agent-stream-eval-20260818-b`

The local WAV replay retained 120 seconds of audio and 9 authoritative transcript paragraphs on the left. The right-hand coach rendered one evidence-grounded intervention:

- Title: `回到刚才的问题`
- Event: `communication_clarity`
- Evidence: `vad_endpoint_007`, `vad_endpoint_008`, `vad_endpoint_009`
- Actionable phrase: answer `我做得对吗` before continuing the unrelated quotation
- Runtime: 7 checklist items, 1 agent turn, 1 `submit_intervention`, 9,592 total tokens, about 7.2 seconds
- Screenshot: `artifacts/tmp/pi-agent-agent-eval-20260819/long-audio-pi-coach.png`
- Existing 8766 history view with three retained prior interventions: `artifacts/tmp/pi-agent-agent-eval-20260819/current-coach-page.png`

This is the useful product behavior: Pi decides whether to interrupt, validates verbatim evidence, and turns a live problem into one sentence the user can say immediately. It is not a second meeting summarizer.

## Cost and latency guardrails

The acceptance run showed that repeated Pi session history was the main avoidable cost. The bridge now keeps at most two previous user turns. The host sends at most eight historical paragraphs and two semantic windows in the initial request; older evidence remains available through the bounded `search_prior_evidence` tool. Fresh paragraphs, rolling state, evidence validation, and the two-turn/four-tool agent budget are unchanged, so the ASR path is not involved.

The provider did not return a price in this run. Use this formula once the provider price is configured:

`input_tokens / 1,000,000 * input_price + output_tokens / 1,000,000 * output_price`

The next acceptance gate is a same-input Direct-vs-Pi replay measuring intervention precision, evidence coverage, first-response latency, and total tokens. No direct result is claimed in this addendum because the running desktop process keeps its API key in memory and does not expose it to test scripts.

## Replay blocker observed in the follow-up pass

A second isolated 60-second silent replay was started against a fresh meeting after the context guardrail change. It did not produce a model call: the WebSocket waited for the final stream event until the 184-second command timeout, and the server log showed repeated `asr_refiner.py` worker start/stop `OSError` messages. The meeting had no LLM usage rows. This is an ASR refiner lifecycle failure in the replay harness, not evidence that Pi failed to reason over the transcript; the original 120-second replay already reached a real Pi intervention. The next long-audio acceptance should fix or bypass that refiner lifecycle first, while leaving the left transcript projection unchanged.
