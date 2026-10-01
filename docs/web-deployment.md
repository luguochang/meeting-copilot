# 完整网页版部署、模型下载与默认效果

日期：2026-10-01。适用分支：`feat/pi-realtime-coach-agent-loop`。配套 [完整架构与 Pi 职责](architecture-current-pi.md)。

## 1. 不安装客户端，怎么交给别人用

每个人在自己的电脑上安装运行环境，启动本地服务，用浏览器打开工作台。前端构建后由同一个后端托管，不必每次运行 Vite，也不需要 Rust/Tauri。

需要本分支源码、Node、uv/Python、两套 Python 依赖、前端/Pi npm 依赖和语音模型。接收者自己配置远端 Provider；不要复制发送者的数据目录、Key、录音和会议数据库。

这不是“发一个公网网址，所有人免安装共用你的电脑”。当前产品是本机单用户服务；多人托管还需要认证、隔离、配额、HTTPS/WSS 和音频上传方案。

## 2. 本地模型的公开下载来源

都是语音专用模型，不是聊天大模型。2026-10-01 已检查上游页面可访问；页面可访问不等于所有平台均完成推理验收。脚本对推理文件执行 SHA-256 校验。

| 目录 | 官方模型入口 | 用途 | 完整配置 |
| --- | --- | --- | --- |
| `online` | [Paraformer 流式模型](https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online) | 边说边显示文字 | 必需，权重约 881 MB |
| `offline` | [SeACo-Paraformer](https://www.modelscope.cn/models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch) | 本地段落精修、录音文件转写 | 必需，推理文件约 998 MB |
| `vad` | [FSMN VAD](https://www.modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch) | 语音活动、分段、说话人旁路 | 必需，约 1.7 MB，标签 `v2.0.4` |
| `punc` | [CT-Transformer 标点](https://www.modelscope.cn/models/iic/punc_ct-transformer_cn-en-common-vocab471067-large) | 恢复断句与标点 | 必需，推理文件约 1.19 GB |
| `camplus` | [FunASR 兼容 CAM++](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common) | 实时说话人嵌入与聚类 | 包含，约 28 MB，标签 `v2.0.2` |

推理文件合计约 3.1 GB（十进制），另需 Python/Node、依赖、缓存和录音空间。建议预留 10 GB 磁盘、16 GB 内存；这是建议，不是已完成低配机器性能认证。

### 固定版本与已有清单的坑

- offline/vad/punc 哈希来自 `code/asr_runtime/model_packs/file-asr-zh-cn-20260718.manifest.json`。
- CAM++ 来自 `diarization-camplus-zh-cn.manifest.json`；online 哈希在 `tools/web_quality.py`，与当前本机缓存核对。
- 部分上游用 `master` 作为下载定位，但实际版本身份由文件哈希决定。上游变更导致不匹配时拒绝继续，不能为了下载成功就跳过校验。
- 实际下载发现，旧受控打包清单的 `damo/... @ v1.0.0` 缺少 FunASR 需要的 `config.yaml`，配置也不同。新版下载入口改用已有验证记录的 `iic/... @ v2.0.2`。旧客户端打包清单仍需修正并重新验收，不能照搬。
- 模型有公开来源和 Apache-2.0 许可线索；个人从上游下载与我们对外分发含模型的安装包不同。已有打包清单的部分公开再分发核验仍未完成，不能把它说成已经完成。

## 3. 首次安装

先安装 [Git](https://git-scm.com/downloads)、[Node.js 22.19 或更高](https://nodejs.org/en/download)、[uv](https://docs.astral.sh/uv/getting-started/installation/)。建议同批使用相同 Node 22 LTS 补丁版。Python 由 uv 安装。

以下命令从仓库根目录执行，Mac 与 PowerShell 均可运行：

也可解压本轮 `Talktrace-web-source-20261001.zip`，进入 `Talktrace-web`，跳过下面的 `git clone` 和 `cd meeting-copilot`。这份源码包已包含新脚本；若远程分支尚未同步本轮提交，直接克隆旧版本会找不到 `tools/web_quality.py`。

```bash
git clone --branch feat/pi-realtime-coach-agent-loop --single-branch https://github.com/luguochang/meeting-copilot.git
cd meeting-copilot
uv python install 3.13 3.11
uv sync --project code/web_mvp/backend --frozen
npm --prefix code/web_mvp/frontend_v2 ci
npm --prefix code/web_mvp/frontend_v2 run build
npm --prefix code/agent_runtime/pi_coach_bridge ci
uv venv --python 3.11 code/asr_runtime/.venv-funasr
```

Mac/Linux 语音依赖：

```bash
uv pip install --python code/asr_runtime/.venv-funasr/bin/python -r code/asr_runtime/requirements-funasr.lock
```

Windows PowerShell 语音依赖：

```powershell
uv pip install --python code/asr_runtime/.venv-funasr/Scripts/python.exe -r code/asr_runtime/requirements-funasr.lock
```

锁文件是本分支基线；不同 OS/CPU 的 wheel 可用性仍需对应平台验证。遇到依赖错误应记录并修复，不能删除精修依赖后把“页面能打开”称为完整交付。本轮未在全新 Windows、Intel Mac 或 Linux 电脑上完成全流程验收。

## 4. 下载、检查、启动：固定入口

```bash
# 下载缺失模型；哈希一致的已有缓存会复制到固定目录。
uv run --project code/web_mvp/backend python tools/web_quality.py models

# 检查模型哈希、前端、Python/FunASR/FFmpeg、Node 与 Pi bridge。
# 不请求远端 AI，不采集麦克风，不播放声音。
uv run --project code/web_mvp/backend python tools/web_quality.py doctor

# 启动本地服务，包含精修和 Pi 预热。
uv run --project code/web_mvp/backend python tools/web_quality.py start
```

打开 [http://127.0.0.1:8765/workbench](http://127.0.0.1:8765/workbench)。

下次只执行 `start`。缺文件、哈希不符或依赖/Pi 检查失败时会退出，不把只能录音、不能精修的情况冒充完整效果。

```bash
uv run --project code/web_mvp/backend python tools/web_quality.py status
uv run --project code/web_mvp/backend python tools/web_quality.py stop
```

模型在 `data/local_runtime/web-models/`，会议、设置、日志和 PID 在 `data/local_runtime/web-quality/`，均被 Git 忽略。无需修改任何写死的开发者电脑路径。

换端口或数据盘：

```bash
uv run --project code/web_mvp/backend python tools/web_quality.py start --port 8768 --model-root /absolute/path/models --data-dir /absolute/path/meetings
```

Windows 可用 `D:/Talktrace/models` 和 `D:/Talktrace/meetings`。`status`、`stop` 传相同端口与数据目录。不同实例必须使用不同数据目录；不要共享同一个会议数据库。脚本不会强杀未知占端口进程。

旧 `workbench_server.py` 保留开发用途。裸 `uvicorn` 默认可能是 `online_only`，表示只做流式本地识别、跳过离线段落精修，**不表示云端 ASR**。新版固定入口显式开启精修，避免启动命令不同导致效果下降。

## 5. 默认能力与核对方式

| 能力 | 完整启动配置 | 验证 |
| --- | --- | --- |
| 实时识别 | 固定 PyTorch FunASR 和 online 模型 | `/providers/health` realtime ASR 可用 |
| 本地精修 | `MEETING_COPILOT_REALTIME_REFINER_POLICY=prewarm`，固定 offline/vad/punc | 不只开关为 on，还要预热 ready |
| 标点和术语规范化 | 标点模型；新工作区 `l3_normalize_enabled=true` | `/settings` 与修订结果 |
| 远端文字校对 | 新工作区 `l2_correction_enabled=true`，Provider 可用后执行 | 校对任务、原文与修订；不是每段都必须改字 |
| Pi 教练 | runtime=pi、enabled=1、bridge 预热，关闭本地 reflex 优先短路 | tool calling 测试和任务 runtime_used=pi |
| 快答 | 保留现有问句/讨论触发规则，实时就绪预算 8 秒 | 分开看首字与完成时间，非端到端 SLA |
| 录音文件转写 | 显式 batch worker、模型、FFmpeg | 实际导入完成 |
| 实时说话人区分 | 固定 VAD/CAM++ 和 worker，现有旁路处理 | 实际多人输入和运行状态；不保证实名 |
| 摘要、纪要、询问 AI | 保留现有入口和后台任务 | Provider 配置、任务完成、文档可读 |

已有工作区曾手动关闭的校对/建议设置不会被脚本悄悄覆盖。交付检查设置页或 `GET /settings`；会议级建议策略、资源降级和预算同样会影响能力。

精修运行状态可查看 `GET /providers/asr/runtime` 的 `offline_refinement.capability` 和 `offline_refinement.worker`。空闲 120 秒后模型可以卸载以释放内存，开始下一场时重新加载；这与配置关闭不同，不能只在空闲时看到 process_running=false 就判定没启用。

脚本不把他人的付费服务标为 `unmetered`，不取消预算。当前校对策略在两项费率都未填写时允许处理并标注费用未知；若配置费率则需两个合法值。Key 余额、模型限速、网络质量仍会影响效果。

## 6. 由接收者配置远程模型

在“AI 设置”填写 Base URL、API Key、API 风格和模型，测试连接及 Pi 工具调用能力，然后保存。普通文本响应成功不能替代 tool calling 测试。

通用、实时、校对模型都要确认。只迁移通用模型而漏掉其他两项可能换用回退模型。当前 `gpt-6-sol` 是你配置的网关型号，其他人的服务未必有同名模型，不能强填。

适配器对 `gpt-6-sol` 将 `thinkingLevel=off` 映射成 `reasoning.effort=low`；其他模型参数支持由适配器判断，不能泛化所有服务都支持。

Pi 不附送模型额度，不要求另开 Pi 模型账号，使用同一套 Provider。Key 留在接收者本机，不写入源码、前端、模型包或共享说明。

## 7. 网页版与客户端差异

| 使用场景 | 本地网页 | Tauri 客户端 |
| --- | --- | --- |
| 麦克风、精修、快答、Pi | 支持，需浏览器麦克风权限 | 支持 |
| 导入文件、会后整理、询问 AI | 支持 | 支持 |
| 其他应用的系统声音 | 当前网页代码没有该采集实现 | 原生系统音频适配器，需 OS 权限 |
| 麦克风与系统声音双轨 | 当前依赖原生桥，纯网页不能保证 | 对应平台实现 |
| 运行环境 | 接收者自行安装 | 完整包应自带 |
| Key 存储 | 本地后端私有配置文件 | 系统安全存储桥 |

戴耳机开视频会议时，只录麦克风通常听不到对方，Pi 因此拿不到对方问题；不是打开精修就能解决。需要客户端双轨，或另行实现浏览器/虚拟声卡方案。本轮没有增加浏览器系统音频功能。

## 8. 新电脑交付 checklist

- [ ] 同一 Pi 分支/提交，前端与 bridge 的 npm 锁定安装成功。
- [ ] 五类模型的推理文件哈希匹配；`doctor` 完全通过。
- [ ] 实际本地精修预热成功，不只是配置为 prewarm。
- [ ] 通用/实时/校对 Provider 配置正确，Pi 工具调用测试通过。
- [ ] 校对、规范化、主动建议开启；已有工作区未遗留关闭设置。
- [ ] 固定公开/合成音频静默测试转写与精修，再验证真实麦克风；无授权不外放。
- [ ] 明确问题与连续讨论都出可用快答，同题 Pi 补充保留原回答。
- [ ] 调整后导航往返、历史、导出完整。
- [ ] 短 WAV 和压缩文件导入成功，校对与文档各自任务完成。
- [ ] 断网保留录音原文；失败状态明确，不伪装为无需介入。
- [ ] 重启后文字修订、会议、历史和文档仍在。

相同效果需要相同代码、模型文件、配置和输入。硬件、麦克风、音轨与 Provider 有差异，脚本能防止配置/文件缺失导致的静默降级，不能承诺任意机器上的识别率与时延完全一致。

## 9. 待制作安装包

新的 Pi 完整安装包尚未完成。历史 Windows base 包不含语音模型；旧 macOS 打包流程未完整纳入 Node/Pi bridge，本机也没有 Developer ID 签名证书。文档完成不等于安装包完成。

客户端后续应包含 Python、Node、Pi bridge、语音模型和原生助手，复用本文默认配置，在新数据目录验收，并处理平台签名。需修正旧 CAM++ 打包来源。接收者 OS/CPU 尚需明确，才能产出匹配的安装文件。

## 10. 本轮实际验证记录

- 本机 Apple Silicon Mac，沿用现有已安装依赖，没有声称在空白电脑上重新安装成功。
- online/offline/vad/punc 从本机已有公开模型缓存复制并验证哈希；CAM++ 从 ModelScope 实际下载后验证。所有五类模型的推理文件校验通过。
- `doctor` 的前端、五类模型、后端/FFmpeg、FunASR 导入、Node 版本及 Pi bridge 检查全部通过；bridge smoke 使用测试 Provider，不消耗远端额度。
- 新目录 `artifacts/tmp/web-quality-acceptance-20261001`、端口 8994 启动成功。`/providers/asr/runtime` 确认实时 resident 与离线 refiner 的 `process_ready=true`，精修模型加载约 13.7 秒、热词 22 个、`network_offline=true`。
- 新工作区 `/settings` 确认 `l2_correction_enabled`、`l3_normalize_enabled`、`suggestions.enabled` 均为 true。
- VAD/CAM++ 在 FunASR 中实际加载成功，不只是检查文件名。没有在本轮做多人聚类精度评测。
- 将上游公开的 `speaker1_a_cn_16k.wav`（3.715 秒）通过正式 `/v2/meetings/import-audio` 导入，任务 succeeded/completed/100%，得到持久化的非空转写；这是功能测试，不是识别准确率基准。
- 此隔离实例没有配置远端 Key，所以没有把远端校对/摘要的 pending 状态算作成功，也没有在这次部署测试中重新验证真实 Provider 输出。
- 5 项部署工具回归、Ruff 和 diff 空白检查通过。没有麦克风采集或外放，没有修改 main 分支。
- 独立验收服务已关闭。收尾发现原 8991 已停止监听，使用既有 `artifacts/tmp/web_mvp_ui_review` 数据目录恢复服务并应用完整质量配置；原会议与 Provider 设置保留。当前开发机复用命令见 [启动手册](pi-workbench-startup.md)。
- 恢复后的 8991 再次确认 realtime/refiner 均 ready，校对/规范化/建议开启；真实 `gpt-6-sol` 的 Pi tool-call 连接检测成功，约 4438ms，`tool_call_ready=true`、`realtime_ready=true`。这是连接与工具协议探测，不是整场会议效果或 P95 延迟验收。
