# V2 录音导入闭环

日期：2026-07-17

## 目的

把“导入一段本地录音并进行会后复盘”接入 V2 的 canonical meeting 模型，避免旧版文件转写接口只写 legacy session、V2 历史页面却看不到结果。

该能力是实时麦克风主链路的补充，不替代实时会议：

```text
本地文件 -> 本地 FunASR batch -> V2 transcript -> correction/suggestion jobs
         -> V2 recording/audio -> end meeting -> minutes/approach/index -> history/review
```

## 接口契约

### `POST /v2/meetings/import-audio`

请求：`multipart/form-data`，字段 `file`。

约束：

- 最大原始文件大小：500 MB。
- 音频由本地 FunASR batch worker 处理；未准备好本地 FunASR 时返回 `422`。
- 文件先落临时文件，再在线程池中执行阻塞转写和 WAV 归一化，不阻塞 FastAPI event loop。
- 标准化播放文件为 `audio_assets/{meeting_id}/audio.wav`。
- 原始上传文件保留在同一会议目录下的 `source.<suffix>`，删除会议时由同一删除栅栏清理。
- API Key、原始音频和转写内容不发送到远程 ASR；会后 LLM 作业仍遵循 Provider 设置。

成功响应 `201` 至少包含：

```json
{
  "meeting_id": "import_<id>",
  "source": "uploaded_file",
  "provider": "local_funasr_batch",
  "raw_transcript": "...",
  "transcript": "...",
  "audio": {"relative_path": "audio_assets/<id>/audio.wav", "sha256": "..."},
  "source_audio": {"relative_path": "audio_assets/<id>/source.m4a", "sha256": "..."},
  "snapshot": {"meeting_id": "...", "segments": [], "jobs": []},
  "jobs": []
}
```

当前阶段采用同步 `201`，因为 Phase 0 目标是先把本地导入主链跑通；生产发布前仍需将长任务提升为 durable import job（`202` + 状态查询/恢复），并增加 `Idempotency-Key`，避免客户端超时重试造成重复会议。这是明确 backlog，不把当前同步实现写成生产级异步任务系统。

失败边界：

- 文件超过限制：`413`。
- 本地 ASR、转码或 WAV 校验失败：`422`。
- 持久化目录不可用：`503`。
- 会议已经创建但后续步骤失败：执行 V2 deletion fence，避免留下半套 meeting/audio。

## 持久化语义

- canonical transcript 通过现有 `_commit_v2_final()` 写入 `transcript_segments` 和 outbox，并入队 correction/suggestion durable jobs。
- 导入录音通过 `V2Persistence.register_imported_recording()` 登记为 `microphone/epoch=0/status=ready`，用一个完整 WAV 记录到 `audio_chunks`，因此复用现有 `/v2/meetings/{id}/audio` 和 `/audio/content` 读取路径。
- legacy `asr_live_repo` 同时写入一份非 mock 的 `local_funasr_batch` projection，满足现有 post-meeting correction/minutes/approach handler 的兼容要求。
- `end_meeting()` 负责创建 correction、minutes、approach、index 作业；接口只唤醒 durable executor，不同步等待大模型结果。
- UI 导入成功后打开同一个 `meeting_id` 的复盘页面，页面通过 snapshot/events 继续显示作业状态。

## 当前限制

- 当前 batch wrapper 对外主要提供整段 `text`；导入会话会提交一个 whole-file canonical segment。FunASR worker 若后续稳定提供多段时间戳，应在该接口加入 segment schema 和整批事务提交，改善证据定位粒度。
- 导入是会后/文件链路，不代表实时 partial 字幕。实时会议仍使用浏览器或 Tauri native microphone -> WebSocket -> realtime ASR。
- 真实打包页面的“点击开始会议 -> 麦克风权限 -> 实时文字/建议 -> 结束复盘”仍需解锁 Mac 后单独验收，不能用本接口结果替代。

## 验证

已通过：

- 后端 V2 import integration：`1 passed`，并验证 correction/suggestion/minutes/approach/index durable jobs 全部完成。
- V2 app/persistence/recording focused suite：`56 passed`。
- 前端完整 suite：`54 passed`；其中 API/workbench focused suite `23 passed`。
- 前端 typecheck、production build、backend Ruff：通过。

核心证据覆盖：上传、真实文件 hash、canonical WAV、V2 snapshot、ended meeting、correction/suggestion/minutes/approach/index、history、audio metadata、audio content playback、前端 multipart boundary 和导入后路由。
