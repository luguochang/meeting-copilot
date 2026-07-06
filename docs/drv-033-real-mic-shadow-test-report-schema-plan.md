# DRV-033 Real Mic Shadow Test Report Schema Plan

> 日期：2026-07-03  
> 状态：Implemented and verified as schema-only/no-audio report gate  
> 范围：固定用户最终真实麦克风 shadow test 的报告结构，防止真实验收退化成口头判断或普通转写验收。  
> 边界：本文档和工具不授权访问麦克风、不授权枚举设备、不授权请求权限、不授权读取真实用户录音、不授权写入或删除真实 audio chunk、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权运行 Cargo/Tauri。

## 1. 目的

真实麦克风会议必须由用户最终显式执行，但在进入真实会议前，需要先把验收报告 schema 固定下来。否则后续即使跑了真实会议，也可能只得到一段 transcript 和主观描述，无法判断产品是否真的比普通转写工具有价值。

DRV-033 固定的验收输出：

- transcript
- ASR metrics
- EvidenceSpan timeline
- state timeline
- candidate/card timeline
- feedback labels
- Go/Pivot/Stop final decision
- privacy/cost flags
- audio retention/delete status
- known limitations

## 2. 实现

新增：

- `tools/real_mic_shadow_test_report_schema.py`
- `tests/test_real_mic_shadow_test_report_schema.py`
- `docs/drv-033-real-mic-shadow-test-report-schema-plan.md`

工具能力：

- 无 candidate report 时输出 schema contract。
- 有 candidate report 时只校验 JSON 结构和安全字段。
- 固定 EvidenceSpan、state 和 candidate/card timeline 的必填字段。
- 校验 EvidenceSpan `segment_id` 必须引用 transcript segment，state/card 必须引用 EvidenceSpan。
- 校验 feedback aggregate 必须等于固定标签的计数汇总。
- `go` 决策必须至少有 2 个 `useful` 或 `would_have_asked` 反馈，且 negative feedback 不超过 1。
- 支持 approved report root：`artifacts/tmp/real_mic_shadow_reports/`。
- 当前 pre-pilot audio chunk root 固定为 `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`。
- 拒绝 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 和 `outputs` 路径。
- 拒绝 dangerous privacy/cost flags，例如 `raw_audio_uploaded=true`、`remote_asr_called=true`、`configs_local_read=true`。
- 拒绝 unsafe audio retention，例如 `not_deleted` 或 `keep_forever`。

反馈标签固定为：

- `useful`
- `would_have_asked`
- `wrong`
- `too_late`
- `too_intrusive`
- `dismissed`

最终决策固定为：

- `go`
- `pivot`
- `stop`
- `inconclusive_requires_more_shadow_tests`

## 3. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_real_mic_shadow_test_report_schema.py \
  -q -p no:cacheprovider
```

结果：

```text
7 failed, 1 warning
```

失败原因：`tools/real_mic_shadow_test_report_schema.py` 尚不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_real_mic_shadow_test_report_schema.py \
  -q -p no:cacheprovider
```

结果：

```text
7 passed, 1 warning
```

复审加固红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_real_mic_shadow_test_report_schema.py \
  -q -p no:cacheprovider
```

结果：

```text
6 failed, 7 passed, 1 warning
```

失败原因：初版 schema 只校验 timeline item 是 object，未固定 nested fields 和 cross references；feedback aggregate、`go` 决策、audio retention enum 和 sanitized candidate path 也未收紧。

复审加固绿灯：

```text
13 passed, 1 warning
```

## 4. 安全边界

DRV-033 工具只做 schema/report validation，所有执行 safety flags 保持 false：

- `safe_to_access_microphone_now=false`
- `safe_to_enumerate_audio_devices_now=false`
- `safe_to_request_audio_permission_now=false`
- `safe_to_read_real_user_audio_now=false`
- `safe_to_write_audio_chunk_now=false`
- `safe_to_delete_audio_chunk_now=false`
- `safe_to_read_configs_local_now=false`
- `safe_to_call_remote_asr_now=false`
- `safe_to_call_llm_now=false`
- `safe_to_run_tauri_or_cargo_now=false`
- `safe_to_mutate_web_session_now=false`

## 5. 后续

DRV-033 完成后，真实麦克风会议仍未开始。下一步应继续从 6 个里程碑中选择：

1. ASR 质量一次性决策。
2. Real Tauri no-op run。
3. Worker output -> Web Live ASR session 闭环。
4. Mic adapter no-op UI invocation。
5. Short local simulated input。
6. 用户最终真实麦克风 shadow test。

真实 shadow test 必须等 desktop runtime、mic adapter start/pause/resume/stop/delete、ASR worker handoff、导出和反馈链路具备后，由用户显式启动。
