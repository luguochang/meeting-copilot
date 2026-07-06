# Local Artifact Retention And Delete Boundary

> Date: 2026-07-04  
> Decision: DEC-210  
> Stage: Local retention/delete boundary implemented  
> Scope: 本地主链路自测产物、音频 health sample、未来真实音频 chunk/report 的保留与显式删除边界

## 1. Product Decision

Meeting Copilot 的产品价值需要保留会议录音、转写和报告用于复盘，但这必须和删除语义一起交付。

本轮实现的是本地 artifact retention/delete 最小闭环：

```text
approved local artifacts
  -> retention manifest
  -> explicit delete-only cleanup
  -> no private path read
  -> no audio content read
```

这不是云端保留策略，也不删除第三方 ASR/LLM provider 已处理的数据。当前只覆盖本地 ignored artifact roots。

## 2. Implemented Files

```text
tools/local_artifact_retention.py
tests/test_local_artifact_retention.py
tools/mainline_usable_e2e_runner.py
tests/test_mainline_usable_e2e_runner.py
```

主链路 runner 现在会在报告中写入：

```text
artifact_retention.retention_status
artifact_retention.retained_artifact_count
artifact_retention.deleted_artifact_count
artifact_retention.blocked_artifact_count
```

默认状态：

```text
local_artifacts_retained
```

## 3. Approved Roots

Retention/delete 只允许这些 ignored runtime roots：

```text
artifacts/tmp/audio_health/
artifacts/tmp/mainline_selftests/
artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/
artifacts/tmp/real_mic_shadow_tests/
artifacts/tmp/real_mic_shadow_reports/
artifacts/tmp/asr_events/
artifacts/tmp/asr_reports/
```

Forbidden roots remain blocked before delete:

```text
configs/local/
data/asr_eval/local_samples/
data/local_runtime/
outputs/
repo outside paths
```

## 4. CLI

Retention report only:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/local_artifact_retention.py \
  --session-id session-a \
  --artifact-path artifacts/tmp/audio_health/session-a.mainline-health.wav
```

Explicit delete:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/local_artifact_retention.py \
  --session-id session-a \
  --artifact-path artifacts/tmp/audio_health/session-a.mainline-health.wav \
  --delete
```

The tool records file path, existence, and size only. It does not read audio/report contents.

## 5. Safety Contract

The tool does not:

- read raw audio content;
- upload audio;
- call remote ASR;
- call LLM;
- read `configs/local`;
- read private local audio directories;
- use paid providers;
- delete anything outside approved roots.

All privacy/cost flags remain false:

```text
raw_audio_uploaded=false
remote_asr_called=false
llm_called=false
configs_local_read=false
private_user_audio_read=false
paid_provider_used=false
```

## 6. Verification

TDD red:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py -q -p no:cacheprovider
Result: 4 failed because tools/local_artifact_retention.py did not exist
```

Focused green:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py -q -p no:cacheprovider
Result: 4 passed, 1 warning
```

Mainline integration:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 13 passed, 2 warnings
```

Adjacent regression:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py tests/test_mac_system_audio_capture_adapter.py tests/test_audio_capture_healthcheck.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 32 passed, 2 warnings
```

Syntax:

```text
python3 -m py_compile tools/local_artifact_retention.py tests/test_local_artifact_retention.py tools/mainline_usable_e2e_runner.py tests/test_mainline_usable_e2e_runner.py
Result: exit 0
```

## 7. Remaining Work

This closes the local artifact retention/delete boundary for current ignored runtime artifacts.

Remaining production work:

- Add UI controls for pause/stop/delete when the desktop capture runtime becomes active.
- Extend deletion to production user data directory once real meeting storage is introduced.
- Keep third-party provider retention disclosures in provider settings.
- Run real Mac system-audio health capture only after explicit user authorization.
