# Release Acceptance Checklist

> 日期：2026-07-08
> 状态：P0-5 implemented / release gate required before any publish claim
> 范围：本文件定义发布前必须跑的本地验收命令和 No-Go 条件。`tools/release_acceptance_runner.py` 只汇总功能 lane；最终发布还必须由 `tools/release_provenance_manifest.py` 绑定源码、产物、证据和供应链状态。

## 1. 原则

- Release acceptance runner 是汇总器，不复制 ASR、LLM、UI lane 业务逻辑。
- ASR/LLM/UI 主链路证据仍由 `tools/mainline_evidence_bundle_runner.py` 生成。
- 任一 required check 或 required lane 失败，release verdict 必须是 `no_go`。
- Browser live mic 必须有 `browser_live_mic_go_evidence=true`，不能用 `real_mic_recorded_wav` 替代。
- `browser_live_mic_not_proven` 在 release 层必须映射为 `blocked_browser_live_mic_not_proven`。
- 默认不读取 `configs/local/`，不读取未授权私人音频，不启用远程 ASR。
- 功能 lane 全部 Go 不能覆盖 dirty source、旧证据、产物哈希、许可证、SBOM 或模型 provenance 的 No-Go。

## 2. Required Checks

`tools/release_acceptance_runner.py` 默认执行以下检查：

```text
pytest_backend_mainline
workbench_smoke
git_diff_check
health_endpoint
workbench_js_version
```

阻断条件：

```text
check_<name>_missing
check_<name>_failed
```

## 3. Required Lanes

Release runner 默认汇总以下 lane：

```text
file_lane
simulated_realtime
browser_live_mic
```

其中 `file_lane`、`simulated_realtime` 和 `browser_live_mic` 是当前发布功能门禁的 required lanes。
`real_mic_recorded_realtime` 是可选的录音回放补充 lane：它用于绑定一份已授权的真实录音和健康报告，
但不能因为该 lane 缺失或 `no_go` 就被 browser live mic 的正向证据替代，也不能反过来替代 browser live mic。
runner 仍会生成该 lane 的显式 blocked manifest，避免“未提供证据”被误读为“已通过”。

每个 lane 必须至少汇总：

```text
verdict
audio_source
asr_provider
asr_provider_mode
asr_fallback_used
llm_provider
transcript_char_count
final_segment_count
suggestion_card_count
approach_card_count
minutes_char_count
delete_verified
asr_semantic_quality_status
asr_semantic_quality_blocked
degradation_reasons
artifact_root
manifest_path
go_no_go_path
```

## 4. Required Output

每次运行必须生成：

```text
artifacts/tmp/release_acceptance/<run-id>/summary.json
artifacts/tmp/release_acceptance/<run-id>/report.md
```

`summary.json` 顶层必须至少包含：

```text
schema_version=release_acceptance.v1
run_id
started_at
ended_at
git_commit
artifact_root
verdict
blockers
privacy_cost_flags
artifacts.summary_json
artifacts.report_md
checks
lanes
```

## 5. 默认命令

发布候选命令示例：

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/release_acceptance_runner.py \
  --run-id local-release-candidate \
  --real-mic-recorded-audio artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.wav \
  --real-mic-health-report artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.health.json \
  --browser-live-mic-bundle artifacts/tmp/acceptance/p0-browser-live-mic-tech-audio-20260708-231952
```

如果没有 browser live mic bundle，命令仍会写报告，但 verdict 必须是 `no_go`，并包含：

```text
blocked_browser_live_mic_not_proven
```

## 6. Fixed Verification

P0-5 focused verification：

```bash
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_release_acceptance_runner.py \
  tests/test_mainline_evidence_bundle_runner.py

python3 -m py_compile \
  tools/release_acceptance_runner.py \
  tools/mainline_evidence_bundle_runner.py
```

发布候选验证：

```bash
PYTHONPATH=code/web_mvp/backend:code/core python3 tools/release_acceptance_runner.py \
  --run-id <release-run-id> \
  --real-mic-recorded-audio <authorized-runtime-wav> \
  --real-mic-health-report <authorized-runtime-health-json> \
  --browser-live-mic-bundle <browser-live-mic-go-bundle>
```

## 7. Stop Rules

以下任一出现，release verdict 必须是 `no_go`：

- pytest 或 Workbench smoke 失败。
- `/health` 不可访问或非 200。
- Workbench 页面无法解析当前 `workbench.js?v=...`。
- `git diff --check` 失败。
- 任一 lane manifest `verdict != go`。
- `asr_semantic_quality_blocked=true`。
- `asr_provider_mode` 不是 real，或 `asr_fallback_used` 不是 false。
- `llm_provider` 不是 `real_gateway`。
- `delete_verified=false`。
- Browser live mic 缺失或 `browser_live_mic_go_evidence=false`。
- evidence 或 report 泄露 API key。
- `configs_local_read=true` 或 `user_audio_committed_to_repo=true`。

## 8. Release Provenance Gate

功能验收完成后，必须对**显式指定**的候选产物和 evidence 运行：

```bash
python3 tools/release_provenance_manifest.py \
  --run-id <provenance-run-id> \
  --evidence-run-id <evidence-run-id> \
  --artifact <release-artifact> \
  --evidence-manifest <evidence-manifest> \
  --app-name "Meeting Copilot" \
  --app-version <version> \
  --build-id <git-commit>
```

工具只接受 approved repository artifact root；仓库外运行时产物必须显式使用 `--artifact-scope runtime`。它不按 mtime 查找“最新”证据，拒绝 symlink、路径越界、缺失/不匹配哈希和 run id，并将结果写入 `artifacts/tmp/release_provenance/<run-id>/manifest.json`。

以下任一项使最终 verdict 保持 `no_go`：dirty tracked source、untracked source、tracked sensitive path、artifact/evidence 缺失或不匹配、evidence 不允许公开发布、根 LICENSE/NOTICE/SBOM 缺失或无效、模型/FFmpeg revision/hash/redistribution 未解决。`release_acceptance_runner.py` 的历史 `go` 只能解释为功能 lane 通过，不能单独作为公开发布结论。

## 9. 当前边界

- P0-5 完成代表 release runner 已具备固定验收能力。
- P0-5 不代表 P1/P2/P3 已完成。
- 在 P1/P2/P3 完成前，即使 P0 lane 全部 Go，项目状态仍应保持 `Production MVP: Conditional No-Go`。
- 2026-07-15 当前 provenance manifest 为 `no_go`；项目等级仍是 `L0 功能原型`。
