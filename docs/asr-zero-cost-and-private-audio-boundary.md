# ASR Zero-Cost and Private Audio Boundary

> 日期：2026-07-03  
> 状态：Accepted  
> 目的：把“默认不额外收费、默认不读取私人音频、默认不联网调用模型”的边界单独写清楚，避免后续开发偏离。

## 1. 默认不会做什么

默认不会：

- 不读取真实用户音频。
- 不读取 `data/asr_eval/local_samples/`。
- 不读取 `data/local_runtime/`。
- 不读取 `configs/local/`。
- 不访问麦克风。
- 不调用远程 ASR。
- 不调用 LLM 中转站。
- 不调用远程 TTS。
- 不下载 FunASR/ModelScope 模型。
- 不下载 OpenSLR 大体量公开音频包。
- 不把生成音频、公开原始音频、麦克风 chunk 或模型缓存提交到仓库。

## 2. 默认允许做什么

默认允许：

- 读取已提交的 synthetic meeting script。
- 用本机离线 TTS 生成合成音频，输出到 ignored `artifacts/tmp/synthetic_audio/`。
- 读取 ignored `artifacts/tmp/` 下本轮生成的合成音频和 ASR event/report。
- 生成公开音频 no-download plan。
- 读取官方公开数据集网页元信息。
- 运行不访问私人音频、不访问密钥、不调用远程服务的本地测试。

## 3. 什么时候可能产生费用

只有以下动作可能产生远程成本或外部下载成本：

| 动作 | 默认状态 | 进入条件 |
| --- | --- | --- |
| 调用 OpenAI-compatible LLM 中转站 | 禁用 | ASR final/revision、EvidenceSpan、gap candidate 和频率预算稳定后，显式开启 |
| 远程 ASR | 禁用 | 本地 ASR 达不到最低质量，且用户明确选择高质量/对照模式 |
| FunASR 模型下载 | 禁用 | 明确批准模型下载体积、缓存目录、清理策略和收益 |
| OpenSLR 公开大包下载 | 禁用 | 具体 sample extraction plan 通过，并明确下载体积、checksum、清理策略 |

当前默认成本判断：

- 合成音频模拟：零远程费用。
- sherpa 本地 synthetic ASR smoke：零远程费用。
- FunASR readiness/offline guard：零远程费用；执行器已要求显式 `--local-model-dir`，当前仍因本地模型目录/cache 缺失而 blocked，不能继续执行真实 FunASR smoke。
- 公开音频来源复核：只访问网页元信息，不下载音频。
- LLM 中转站：未调用。

## 4. 什么时候才会请求麦克风

麦克风只能在以下条件全部满足后进入：

- Tauri 桌面窗口已运行。
- native IPC 已验证。
- ASR worker contract 已准备好。
- UI 已有 start/pause/resume/stop。
- 用户主动点击 start。

麦克风采集必须支持：

- input device 选择。
- input level。
- chunk count。
- duration。
- pause/resume。
- stop。
- 一键删除本地 ignored chunk。

麦克风采集不得：

- 启动即自动录音。
- 默认上传音频。
- 默认调用远程 ASR。
- 把 chunk 写入仓库。

## 5. 临时文件放在哪里

| 类型 | 目录 | 是否提交 |
| --- | --- | --- |
| 合成音频 | `artifacts/tmp/synthetic_audio/` | 不提交 |
| ASR events | `artifacts/tmp/asr_events/` | 不提交 |
| ASR reports | `artifacts/tmp/asr_reports/` | 不提交 |
| 公开原始音频 | `data/asr_eval/public_raw/` 或 `artifacts/tmp/public_audio/` | 不提交 |
| 桌面构建 target | `artifacts/tmp/desktop_tauri_target/` | 不提交 |
| 真实麦克风 chunk | 当前 pre-pilot 合同使用 `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/`；未来生产用户数据目录另起决策 | 不提交 |

`.gitignore` 已覆盖：

- `configs/local/`
- `data/asr_eval/local_samples/`
- `data/asr_eval/public_raw/`
- `artifacts/tmp/`
- `data/local_runtime/`
- `*.local.json`
- `*.secret.json`

## 6. 报告必须写明的状态

每个 ASR/音频相关报告都应尽量包含：

- `safe_to_read_user_audio=false`
- `safe_to_read_configs_local=false`
- `safe_to_call_remote_asr=false`
- `safe_to_call_llm=false`
- `safe_to_download_models=false`，如果涉及 provider/model。
- `download_status=not_started`，如果涉及公开音频。
- `cost_status=no_paid_remote_service` 或等价字段。

如果某个报告当前还没有这些字段，后续补字段时按 TDD 增加测试，不改变默认禁止行为。

## 7. 用户确认点

以下动作必须另有明确确认，不能从普通开发任务中推断授权：

- 下载 FunASR/ModelScope 模型。
- 下载 OpenSLR 大体量数据包。
- 访问真实麦克风。
- 读取真实用户音频。
- 读取 `configs/local/`。
- 调用 LLM 中转站。
- 调用远程 ASR。
- 安装 Rust 或修改 shell profile。

确认前只允许写计划、写测试、做本地 synthetic smoke、做官方来源网页复核和生成 no-download report。
