# Privacy, Retention, And Delete Policy

> 日期：2026-07-09
> 状态：P1-3 evidence document restored
> 范围：Meeting Copilot 本地 Web MVP、Workbench、release evidence、桌面 runtime artifact 的隐私、保留和删除边界。
> 结论：默认不上传原始音频；远程 ASR 默认关闭；LLM 只在 AI 分析启用时通过 OpenAI-compatible gateway 调用；未授权用户音频、`configs/local/` 和 secret 不得被读取或写入 evidence。

## 1. 默认隐私原则

- 不隐蔽录音。
- 不默认全天监听。
- 不自动加入会议。
- 用户显式点击开始后，才允许进入录音或实时转写流程。
- 录音、转写、建议、纪要和 evidence 的来源必须可解释。
- API key、Authorization、Bearer token、secret、`configs/local/` 路径和本地私有音频不得进入日志、session JSON、evidence bundle 或报告。
- 默认远程 ASR 关闭：`remote_asr_default_enabled=false`。
- 默认不上传原始音频：`raw_audio_uploaded_by_default=false`。

## 2. 数据分类

本地 live ASR session 数据：

```text
MEETING_COPILOT_DATA_DIR/live_asr_sessions/<session_id>.json
```

当前 live ASR session record 可包含：

- `live_events`
- transcript/final/revision events
- suggestion cards
- approach cards
- minutes
- provider metadata
- auto-suggestion state
- source metadata
- degradation reasons

忽略目录中的运行证据和自测产物：

```text
artifacts/tmp/**
code/web_mvp/backend/artifacts/tmp/**
```

这些目录可保存：

- release acceptance evidence
- browser live mic health report
- simulated realtime lane evidence
- real mic recorded lane evidence
- local audio health report
- desktop mic adapter runtime chunk evidence
- ASR event files
- soak reports

这些 artifact 默认是本地 ignored 产物，不应提交到仓库。

## 3. 远程调用边界

LLM：

- 只在用户触发整理会议、自动建议命中候选，或等价 AI 分析流程启用时调用。
- 调用内容应是稳定 transcript、会议状态摘要、EvidenceSpan 和必要技术上下文。
- 不发送原始音频。
- release evidence 需要记录 `llm_called`、`llm_call_count` 和 `llm_usage_total_tokens`。

ASR：

- 默认使用本地 ASR provider。
- 远程 ASR 不是默认能力。
- 如未来启用远程 ASR，evidence 必须显式写入：

```text
remote_asr_called=true
remote_asr_provider=<provider>
raw_audio_uploaded=<true_or_false>
```

禁止：

- 在未授权情况下读取用户 `.m4a`、真实会议录音或私有音频。
- 读取 `configs/local/`。
- 打印、保存或提交 API key、Authorization header、Bearer token。

## 4. 删除语义

`DELETE /live/asr/sessions/{session_id}` 当前删除 live ASR session record，并返回结构化删除范围：

```json
{
  "session_record": "deleted",
  "transcript_events": "deleted_with_session_record",
  "suggestion_cards": "deleted_with_session_record",
  "approach_cards": "deleted_with_session_record",
  "minutes": "deleted_with_session_record",
  "audio": "not_tracked_by_live_session_repo",
  "exports": "not_tracked_by_live_session_repo",
  "evidence_bundle": "not_tracked_by_live_session_repo"
}
```

这意味着：

- session JSON 删除后，API 不应再能读取该 session。
- transcript、suggestion cards、approach cards、minutes 随 session record 删除。
- 当前 live session repository 不追踪用户电脑另存的原始音频文件。
- 当前 live session repository 不追踪 release evidence bundle。
- 当前导出的 transcript/minutes 是按请求生成的 download response，不作为 live session repo 文件长期追踪。

Workbench 删除确认必须说明：

- 将删除本地 session 记录。
- 将删除随 session 保存的文字、建议、方案、纪要。
- 不会删除用户电脑中另存的原始音频。
- evidence bundle 和运行 artifact 需要按本地 artifact retention 工具或人工清理策略处理。

## 5. Local Artifact Retention

`tools/local_artifact_retention.py` 只允许 approved ignored roots 下的 artifact 进入 retention manifest 或显式删除。

允许的代表性 root：

```text
artifacts/tmp/audio_health/
artifacts/tmp/mainline_selftests/
artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/
artifacts/tmp/real_mic_shadow_tests/
artifacts/tmp/real_mic_shadow_reports/
artifacts/tmp/asr_events/
artifacts/tmp/asr_reports/
```

工具边界：

- 不读取 artifact 内容。
- 只记录路径、存在性、大小和 action。
- `--delete` 只删除 approved roots 下的本地 artifact。
- 删除前必须阻断 forbidden roots、repo 外路径和路径穿越。

Forbidden roots：

```text
configs/local
data/asr_eval/local_samples
data/local_runtime
outputs
```

## 6. Evidence Sanitizer

主线 evidence 和 release summary writer 必须做 secret redaction。

当前 sanitizer 需要覆盖：

- `api_key`
- `authorization`
- `token`
- `secret`
- 环境变量中的 `LLM_GATEWAY_API_KEY` 值
- 嵌套对象中的 secret-like text

evidence 允许记录 provider 是否配置、调用次数、token usage、provider mode，但不得记录密钥原文。

## 7. 当前已验证项

当前 P1-3 完成证据包括：

```text
DELETE /live/asr/sessions/{session_id} returns exact delete_scope
tests/test_mainline_evidence_bundle_runner.py secret redaction
tests/test_release_acceptance_runner.py secret redaction
code/web_mvp/backend/tests/test_app.py::test_provider_health_endpoint_masks_llm_secret_and_disables_remote_asr_by_default
code/web_mvp/backend/tests/test_app.py::test_delete_asr_live_session_reports_exact_delete_scope_without_overclaiming
tools/local_artifact_retention.py path guard and approved delete behavior
```

## 8. 当前不能过度宣称

不能宣称：

- 第三方 ASR/LLM provider 侧数据已被删除。
- 用户电脑中另存的原始音频会随 live session 删除。
- 所有 ignored release evidence 都会随 session delete 自动删除。
- Mac native desktop audio chunk lifecycle 已完成。
- Windows installer 或移动端数据删除已实现。

可以宣称：

- Web MVP live session 删除范围是结构化、可测试、不过度承诺的。
- 默认不启用远程 ASR。
- release evidence 不应泄漏 API key。
- 未授权用户音频和 `configs/local/` 不属于默认可读取范围。

## 9. 后续生产补齐项

进入 Mac native desktop MVP 时，必须补齐：

- native audio chunk write/read/delete 的真实生命周期。
- Tauri `mic_adapter.delete_audio_chunks` 与 runtime artifact 的可验证删除证据。
- 安装包数据目录、卸载、删除本地数据的行为说明。
- 长会议 artifact retention 策略。
- 桌面端 privacy UI 和 permission denied/granted 文案。
