# 本地运行记录

> 日期：2026-06-18  
> 目的：记录本机配置、自测结果和当前阻塞点。本文档不保存完整 API key。

## 1. LLM 中转站配置

本地私有配置文件：

```text
meeting-copilot/configs/local/llm-gateway.local.json
```

该目录已被 `.gitignore` 忽略。

当前配置：

- base_url: `<OPENAI_COMPATIBLE_RELAY_BASE_URL>`
- model: `gpt-5.5`
- api_key: 已写入本地私有配置，不在文档展示完整值。

连通性测试：

```bash
cd <repo>/code/asr_bakeoff
python3 -m asr_bakeoff.llm_smoke --config ../../configs/local/llm-gateway.local.json
```

Python urllib 测试结果：

- 失败原因：本机 Python 证书链校验失败，错误为 `CERTIFICATE_VERIFY_FAILED`。

curl 交叉验证结果：

- `/v1/chat/completions` 可用。
- 模型：`gpt-5.5`。
- 返回内容：`LLM 中转站连通。`
- 返回 usage：prompt 34，completion 26，total 60。

结论：

- 中转站接口本身可用。
- Python smoke 脚本需要补 certifi/SSL 上下文处理，或后续改用 `httpx` 并使用系统证书配置。

## 2. 本地私有录音样本（已脱敏）

原始用户文件：

```text
[本地 Apple Voice Memos 临时路径，公开文档已脱敏]
```

已复制到本地忽略目录：

```text
<repo>/data/asr_eval/local_samples/<private-audio>.m4a
```

已转码为 ASR 常用格式：

```text
<repo>/data/asr_eval/local_samples/<private-audio>.16k.wav
```

音频信息：

- 原始格式：m4a / AAC。
- 原始采样率：48kHz。
- 声道：mono。
- 时长：约 352.6 秒。
- 转码格式：WAV / pcm_s16le。
- 转码采样率：16kHz。
- 转码声道：mono。
- 转码后大小：约 11MB。

该目录已被 `.gitignore` 忽略，避免真实录音进入仓库。

## 3. 当前 ASR 状态

本机当前未检测到以下 ASR 依赖：

- FunASR。
- sherpa-onnx。
- Whisper。
- faster-whisper。
- torch。
- modelscope。

因此当前不能声称已经完成本地中文 ASR 转写。

下一步需要：

1. 建立独立 ASR Python 环境。
2. 安装 FunASR 或 sherpa-onnx。
3. 用 `<private-audio>.16k.wav` 跑本地 ASR。
4. 将转写结果进入 LLM 中转站做诊断/修正/建议测试。

## 4. 已验证命令

测试：

```bash
cd <repo>/code/asr_bakeoff
python3 -m pytest tests -q
```

结果：

```text
17 passed, 1 warning
```

音频转码：

```bash
ffmpeg -hide_banner -y \
  -i <repo>/data/asr_eval/local_samples/<private-audio>.m4a \
  -ac 1 -ar 16000 \
  <repo>/data/asr_eval/local_samples/<private-audio>.16k.wav
```

## 5. 结论

- LLM 中转站可用，已通过 curl 实测。
- API key 已写入本地私有配置，未写入可提交文档。
- 用户录音已复制并转码为 ASR 输入格式。
- 本机缺少本地 ASR 依赖，尚未完成真实中文转写。
- 下一步应优先建立独立 ASR 环境并接入 FunASR。

## 6. 2026-06-18 后续自测

### 6.1 Python 版 LLM smoke

已修复 Python urllib 证书链问题：

- `llm_smoke.py` 会优先使用配置中的 CA bundle。
- 未配置时尝试使用 `certifi`。

验证命令：

```bash
cd <repo>/code/asr_bakeoff
python3 -m asr_bakeoff.llm_smoke --config ../../configs/local/llm-gateway.local.json
```

结果：

```json
{
  "model": "gpt-5.5",
  "reply": "LLM 中转站连通。"
}
```

结论：

- Python 版中转站 smoke 已可用。

### 6.2 ASR runtime 基础设施

新增目录：

```text
meeting-copilot/code/asr_runtime/
```

已实现：

- WAV 音频元数据检查。
- transcript report 结构。
- EvidenceSpan 结构。
- LLM meeting analysis 脚本。
- meeting analysis schema 校验。

真实音频检查结果：

```json
{
  "sample_rate": 16000,
  "channels": 1,
  "sample_width_bytes": 2,
  "frame_count": 5641557,
  "duration_seconds": 352.597312
}
```

测试结果：

```text
asr_bakeoff: 18 passed, 1 warning
asr_runtime: 4 passed, 1 warning
```

### 6.3 sherpa-onnx 第一轮安装结果

尝试命令：

```bash
cd <repo>/code/asr_runtime
<python3.11> -m venv .venv-sherpa
.venv-sherpa/bin/python -m pip install -U pip wheel setuptools
.venv-sherpa/bin/pip install sherpa-onnx==1.13.3 soundfile
```

结果：

- macOS arm64 wheel 可解析并开始下载。
- `sherpa_onnx_core` 下载/安装耗时过长。
- 超过 10 分钟仍未完成。
- 已结束该安装进程。
- `.venv-sherpa` 当前约 23MB。
- `import sherpa_onnx` 失败：`ModuleNotFoundError`。

结论：

- sherpa-onnx 路线仍值得保留，但第一轮本机安装被依赖下载耗时阻塞。
- 下一步切 FunASR 隔离环境做文件转写可行性测试。

### 6.4 LLM 结构化分析模拟自测

模拟 transcript：

```text
我们这次 payment-gateway 先灰度 10%，如果错误率超过 0.1% 就回滚。这里还没有确认回滚负责人。张三下周三补充兼容性测试用例。监控指标还需要确认 P99 和错误率。
```

命令：

```bash
cd <repo>/code/asr_runtime
python3 scripts/transcript_report.py \
  --audio simulated-release-review.wav \
  --provider simulated \
  --text-file outputs/simulated-release-review.txt \
  --duration-seconds 45 \
  --latency-ms 1000 \
  --output outputs/simulated-release-review.report.json

python3 scripts/meeting_analysis.py \
  --transcript-report outputs/simulated-release-review.report.json \
  --llm-config ../../configs/local/llm-gateway.local.json \
  --output outputs/simulated-release-review.analysis.json
```

结果：

- 生成 2 个候选决策。
- 生成 1 个行动项。
- 生成 2 个风险。
- 生成 2 个未闭环问题。
- 生成 3 张建议卡片。
- 所有建议卡片均引用 `ev_001`。

结论：

- `gpt-5.5` 中转站可用于结构化会议分析。
- 最小 demo 的“状态机 + 建议卡片 + 证据引用”链路在模拟 transcript 上可行。
- 本小节只证明模拟 transcript 链路；真实 ASR 链路见 6.7 和 6.8。

### 6.5 FunASR 第一轮安装状态

尝试命令：

```bash
cd <repo>/code/asr_runtime
<python3.11> -m venv .venv-funasr
.venv-funasr/bin/python -m pip install -U pip wheel setuptools
.venv-funasr/bin/pip install funasr==1.3.10 soundfile
```

观察：

- FunASR 依赖链明显重于 sherpa-onnx。
- 安装涉及 scipy、librosa、modelscope、huggingface_hub、transformers、jieba 等。
- 这验证了架构判断：FunASR 适合隔离 ASR worker，不适合塞进桌面主进程。

当前状态：

- 安装超过自测等待阈值仍无新进度输出。
- `.venv-funasr` 约 23MB，说明依赖未真正安装完成。
- 已结束该安装进程。
- 尚未完成 FunASR 真实音频转写。

结论：

- FunASR 第一轮被依赖下载阻塞。
- 这不是 ASR 模型效果失败，而是环境依赖获取失败。
- FunASR 仍保留为中文质量优先候选，但必须继续放在隔离 worker 中验证。

下一步建议：

1. 使用更可控的包下载方式，例如配置可靠 PyPI 镜像、预下载 wheel、或使用官方二进制/模型包。
2. 优先选择一个最小依赖的 sherpa-onnx 中文 int8 模型路径做端侧验证。
3. 如果本地依赖继续阻塞，再用远程 ASR 做少量对照，但仍不进入默认产品链路。

### 6.6 sherpa-onnx 安装与模型状态

使用 Python 3.11 隔离环境：

```bash
cd <repo>/code/asr_runtime
<python3.11> -m venv .venv-sherpa
.venv-sherpa/bin/python -m pip install -U pip wheel setuptools -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv-sherpa/bin/pip install sherpa-onnx==1.13.3 soundfile -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证结果：

```text
sherpa_onnx 1.13.3
```

依赖占用：

- `.venv-sherpa`: 约 141MB。
- 中文 int8 模型目录：约 26MB。
- 模型包：约 20MB。

模型：

```text
sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01
```

模型文件：

```text
model.int8.onnx
tokens.txt
bbpe.model
```

结论：

- sherpa-onnx 端侧依赖体积可控。
- 适合 Mac MVP 第一轮“端侧性能和打包可行性”验证。
- 不代表中文技术会议质量已经达标。

### 6.7 真实用户录音本地 ASR

输入：

```text
<repo>/data/asr_eval/local_samples/<private-audio>.16k.wav
```

命令：

```bash
cd <repo>/code/asr_runtime
.venv-sherpa/bin/python scripts/transcribe_sherpa_onnx.py \
  ../../data/asr_eval/local_samples/<private-audio>.16k.wav \
  --model-dir models/sherpa-onnx/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01 \
  --num-threads 2 \
  --chunk-ms 500 \
  > outputs/<private-audio>.sherpa.json
```

结果：

- 音频时长：352.597312 秒。
- ASR 耗时：4058ms。
- RTF：0.011509。
- 输出 segment：18 条。
- 输出模式：`chunked_endpoint_file`。

质量观察：

- 中文可读，但存在 `<unk>`、错字和断句粗糙问题。
- 该录音内容主要是股票/宏观/投资访谈，不是软件技术会议。
- 未命中灰度、回滚、P99、接口、字段、兼容等技术会议关键词。

结论：

- sherpa-onnx 在当前 Mac 上的端侧性能充足，具备进入实时方向的性能基础。
- 该样本不能用于证明“中文技术会议 Copilot”价值，只能证明本地中文 ASR 可运行且速度足够。
- 真实技术会议质量仍需要专门评测集、人工参考稿、热词和 provider bake-off。

### 6.8 真实录音进入 LLM 分析的门禁结果

先生成 transcript report：

```bash
python3 scripts/transcript_report.py \
  --audio ../../data/asr_eval/local_samples/<private-audio>.16k.wav \
  --provider-json outputs/<private-audio>.sherpa.json \
  --duration-seconds 352.597312 \
  --output outputs/<private-audio>.report.json
```

结果：

- `segments`: 18。
- `evidence_spans`: 18。
- `rtf`: 0.011509。

第一版 prompt 曾把该非工程录音误生成 6 张工程建议卡片。

修正：

- 在 `meeting_analysis.py` 增加 `meeting_context.is_engineering_meeting` 门禁。
- 如果是非软件工程会议，允许生成 summary，但 `suggestion_cards` 必须为空。
- validator 拒绝非工程会议中的工程卡片。
- validator 增加建议卡片类型白名单。

复测：

```bash
python3 scripts/meeting_analysis.py \
  --transcript-report outputs/<private-audio>.report.json \
  --llm-config ../../configs/local/llm-gateway.local.json \
  --output outputs/<private-audio>.analysis.gated.json
```

结果：

- `meeting_context.is_engineering_meeting`: false。
- `suggestion_cards`: 0。
- `states.decision_candidates/action_items/risks/open_questions`: 均为 0。

结论：

- 工程语境门禁是 P0 质量边界。
- 产品不能对所有会议都输出工程建议，否则会退化成泛化 AI 助手并产生误报。

### 6.9 合成中文技术会议短样本

为快速验证技术术语识别，使用 macOS 本地中文 TTS 生成短音频：

```bash
say -v Tingting -o outputs/simulated-release-review.aiff -f outputs/simulated-release-review.txt
ffmpeg -hide_banner -y \
  -i outputs/simulated-release-review.aiff \
  -ac 1 -ar 16000 \
  outputs/simulated-release-review.16k.wav
```

输入文本：

```text
我们这次 payment-gateway 先灰度 10%，如果错误率超过 0.1% 就回滚。这里还没有确认回滚负责人。张三下周三补充兼容性测试用例。监控指标还需要确认 P99 和错误率。
```

sherpa-onnx 转写结果：

```text
我们这次先挥百分之十如果错误率超过百分之零点一旧回軚这里还没有确认回滚负责人张三下周三补充兼容性测试用力监控指标还需要确认九九和错误率
```

观察：

- `payment-gateway` 丢失。
- `灰度` 识别为“先挥”。
- `10%` 识别为“百分之十”，可归一化。
- `0.1%` 识别为“百分之零点一”，可归一化。
- `回滚` 在一处识别异常，但 `回滚负责人` 命中。
- `P99` 识别为“九九”。
- `张三`、`下周三`、`兼容性测试`、`监控指标`、`错误率`命中。

LLM 分析结果：

- `meeting_context.is_engineering_meeting`: true。
- 候选决策：2。
- 行动项：1。
- 风险：3。
- 未闭环问题：2。
- 建议卡片：3，分别为 `owner_gap`、`test_verification_gap`、`metric_monitoring_gap`。

结论：

- “ASR + LLM 结构化分析”对技术会议有可行性，LLM 可以部分恢复 ASR 错字带来的语义损失。
- 但 sherpa-onnx 当前模型对英文服务名、灰度、P99 等关键技术实体不够稳，不能单独作为质量最终方案。
- 下一步必须继续验证 FunASR streaming、热词/术语表、数字和指标归一化、以及 LLM second-pass 转写修正。

### 6.10 当前 Go / No-Go 判断

Go：

- Mac 本地 ASR 端侧性能可行。
- LLM 中转站可用。
- 真实录音可以进入 `ASR -> segment -> EvidenceSpan -> LLM` 链路。
- 工程语境门禁已证明可以抑制非工程会议误报。
- 合成技术会议样本可以生成有证据的工程建议卡片。

No-Go / 继续验证：

- 尚未证明真实多人中文技术会议的 ASR 准确率达标。
- 尚未完成 FunASR streaming 本地质量验证。
- 尚未完成真实实时麦克风/系统音频采集。
- 当前 `chunked_endpoint_file` 只验证准实时数据形状，不等价于桌面端真实流式延迟。
- 当前 demo/analysis 链路主要是文件或模拟 transcript，不能单独证明会中 partial storm 下成本可控。
- 技术实体准确率尚不达标，必须引入热词、归一化和 provider bake-off。

### 6.11 2026-06-19 FunASR 本地文件模式实测

环境：

- `.venv-funasr`: 约 1.2GB。
- `funasr==1.3.10`。
- `torch==2.12.1`。
- `torchaudio==2.11.0`。
- Paraformer ASR 模型缓存：约 954MB。
- FSMN VAD 模型缓存：约 3.9MB。
- CT punctuation 模型缓存：约 1.1GB。

命令：

```bash
cd <repo>/code/asr_runtime
.venv-funasr/bin/python scripts/transcribe_funasr.py \
  outputs/simulated-release-review.16k.wav \
  --model paraformer-zh \
  --device cpu \
  --no-punc \
  > outputs/simulated-release-review.funasr.no-punc.v2.json \
  2> outputs/simulated-release-review.funasr.no-punc.v2.log
```

结果：

- 脚本内 latency：约 6255ms。
- report RTF：约 0.348862。
- `/usr/bin/time` wall time：约 6.65s。
- peak memory：约 3.47GB。
- 输出 1 条 `segments`，带 `start_ms=50`、`end_ms=17845`、`is_final=true`。

no-punc 文本：

```text
我 们 这 次 payment gate 为 先 灰 度 百 分 之 十 如 果 错 误 率 超 过 百 分 之 零 点 一 旧 回 滚 这 里 还 没 有 确 认 回 滚 负 责 人 张 三 下 周 三 补 充 兼 容 性 测 试 用 例 监 控 指 标 还 需 要 确 认 t 九 九 和 错 误 率
```

带标点模式：

- 脚本内 latency：约 16952ms。
- report RTF：约 0.945468。
- `/usr/bin/time` wall time：约 17.44s。
- peak memory：约 5.75GB。
- 文本更可读，但 `payment-gateway` 和 `P99` 仍未原样识别。

结论：

- FunASR 文件模式比 sherpa-onnx 更适合作为中文质量候选，但模型和内存成本明显更高。
- `ct-punc` 不适合作为默认实时首轮必开项。
- no-punc 模式必须搭配 transcript normalizer，否则文本可读性和技术实体召回不足。
- 当前文件模式仍不是最终实时方案；下一步必须验证 streaming final segment。

### 6.12 Transcript normalizer 与 demo gate 结果

新增术语表：

```text
meeting-copilot/data/asr_eval/glossaries/technical-terms.zh.json
```

新增字段：

- `normalized_text`。
- `normalization_changes`。
- `raw_technical_entity_recall`。

归一化后文本：

```text
我们这次 payment-gateway 先灰度 10% 如果错误率超过 0.1% 旧回滚这里还没有确认回滚负责人张三下周三补充兼容性测试用例监控指标还需要确认 P99 和错误率
```

normalization changes：

```json
[
  {"alias": "百分之十", "canonical": "10%"},
  {"alias": "百分之零点一", "canonical": "0.1%"},
  {"alias": "payment gate 为", "canonical": "payment-gateway"},
  {"alias": "t 九九", "canonical": "P99"}
]
```

LLM + demo eval：

```json
{
  "is_engineering_meeting": true,
  "state_counts": {
    "decision_candidates": 2,
    "action_items": 1,
    "risks": 2,
    "open_questions": 2
  },
  "suggestion_card_count": 3,
  "suggestion_card_types": [
    "metric_monitoring_gap",
    "owner_gap",
    "test_verification_gap"
  ],
  "state_event_count": 10,
  "unknown_evidence_references": [],
  "raw_technical_entity_recall": 0.0,
  "technical_entity_recall": 0.75,
  "failures": [],
  "passes_minimum_gate": true
}
```

LLM usage sidecar：

```json
{
  "provider": "<OPENAI_COMPATIBLE_RELAY_BASE_URL>",
  "model": "gpt-5.5",
  "prompt_version": "meeting_analysis.v1",
  "call_count": 1,
  "retry_count": 0,
  "usage": {
    "prompt_tokens": 847,
    "completion_tokens": 957,
    "total_tokens": 1804
  }
}
```

关键解释：

- raw ASR 技术实体召回为 0.0，不能宣称 FunASR 文件模式已达中文技术会议质量标准。
- normalized 技术实体召回为 0.75，证明术语/数字归一化能显著改善下游 Copilot。
- demo gate 通过说明产品方向可行，但生产主链路仍必须完成 streaming、热词、更多样本 bake-off。
- normalizer 是默认必选层，但不能掩盖 raw ASR 质量。
- usage sidecar 已记录本次中转站 token 用量，且不包含 API key。

### 6.13 Incremental scheduler 与 mock realtime contract

本轮新增代码：

```text
meeting-copilot/code/asr_runtime/scripts/incremental_scheduler.py
meeting-copilot/code/asr_runtime/scripts/streaming_contract.py
meeting-copilot/code/asr_runtime/scripts/realtime_simulation.py
meeting-copilot/code/asr_runtime/tests/test_incremental_scheduler.py
meeting-copilot/code/asr_runtime/tests/test_streaming_contract.py
meeting-copilot/code/asr_runtime/tests/test_realtime_simulation.py
```

已验证行为：

- ASR `partial` 不触发 LLM 调用，原因记录为 `partial_ignored`。
- 空 `final` 不触发 LLM 调用，原因记录为 `empty_final_ignored`。
- 第一个有效 `final` 可触发 LLM，原因记录为 `final_segment`。
- `revision` 视为稳定更新，可进入调度，但仍受冷却和预算约束。
- 普通 final burst 受 `min_final_interval_ms` 控制，冷却时返回 `cooldown_remaining_ms`。
- 状态变化 segment 可走较短 `min_state_change_interval_ms` 窗口。
- 超过 `max_calls_per_hour` 后返回 `budget_exhausted`，不继续调用 LLM。
- mock streaming contract 只把 `final/revision` 转为正式 provider `segments`，`partial` 只计数不生成正式证据。
- `error/end_of_stream` 进入 provider raw 运行状态和错误列表，不生成正式证据。
- unknown event type 直接失败，避免 provider 适配错误静默吞掉。

可复现命令：

```bash
cd <repo>/code/asr_runtime
python3 -m pytest tests/test_incremental_scheduler.py tests/test_streaming_contract.py tests/test_realtime_simulation.py -q
```

当前结果：

```text
17 passed, 1 warning
```

CLI smoke 示例：

```bash
python3 scripts/realtime_simulation.py \
  --events-json ../../configs/asr_providers/mock-streaming-events.release-review.json \
  --provider mock-stream \
  --min-final-interval-ms 30000 \
  --min-state-change-interval-ms 10000 \
  --max-calls-per-hour 80 \
  --state-change-segment-id seg_owner_gap
```

输出包含：

- `provider_transcript.text`。
- `provider_transcript.segments`。
- `provider_transcript.raw.partial_event_count/final_event_count/revision_event_count`。
- 每个输入事件对应的 `decisions`。
- `llm_trigger_count`。

关键结论：

- 本轮已经证明“不要对每个 ASR partial 调 LLM”的调度边界可以用代码强约束。
- mock streaming provider 是契约/调度器回归工具，不代表 ASR 质量。
- 文件模式 FunASR/sherpa 的 `segments` 只能证明 EvidenceSpan 文件链路可用，不能证明 partial latency、final latency、revision、chunk final 稳定性或桌面实时能力。
- 下一步仍必须接真实 FunASR streaming 或 sherpa streaming，记录 `partial/final/revision` 延迟和多条 chunk final segment。

### 6.14 sherpa-onnx streaming event adapter smoke

本轮新增：

```text
meeting-copilot/code/asr_runtime/tests/test_transcribe_sherpa_onnx.py
```

`transcribe_sherpa_onnx.py` 新增能力：

- `stream_events(...)` 输出统一 `StreamingTranscriptEvent`。
- CLI 支持 `--events-output`，可把真实 sherpa event 序列写成 JSON。
- 直接运行脚本时可正确导入 `scripts.streaming_contract`。
- 原 `transcribe(...)` 仍输出 provider transcript JSON，`raw.mode=file_replayed_streaming_events`，只记录 `model_id`，不记录本地 `model_dir`。

真实模型 smoke 命令：

```bash
cd <repo>/code/asr_runtime
.venv-sherpa/bin/python scripts/transcribe_sherpa_onnx.py \
  outputs/simulated-release-review.16k.wav \
  --model-dir models/sherpa-onnx/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01 \
  --num-threads 2 \
  --chunk-ms 500 \
  --events-output outputs/simulated-release-review.sherpa.streaming-events.json \
  > outputs/simulated-release-review.sherpa.streaming-provider.json
```

真实模型结果：

```json
{
  "text": "我们这次先挥百分之十如果错误率超过百分之零点一旧回軚这里还没有确认回滚负责人张三下周三补充兼容性测试用力监控指标还需要确认九九和错误率",
  "latency_ms": 556,
  "audio_duration_seconds": 17.929,
  "rtf": 0.032071,
  "partial_event_count": 25,
  "final_event_count": 1,
  "end_of_stream_event_count": 1,
  "segment_count": 1
}
```

接入 scheduler：

```bash
python3 scripts/realtime_simulation.py \
  --events-json outputs/simulated-release-review.sherpa.streaming-events.json \
  --provider sherpa-onnx \
  --min-final-interval-ms 30000 \
  --min-state-change-interval-ms 10000 \
  --max-calls-per-hour 80
```

调度结果：

```json
{
  "llm_trigger_count": 1,
  "decision_reasons": {
    "partial_ignored": 25,
    "final_segment": 1,
    "control_event_ignored": 1
  },
  "segment_count": 1
}
```

相关 focused 测试：

```bash
cd <repo>/code/asr_runtime
python3 -m pytest tests/test_incremental_scheduler.py tests/test_streaming_contract.py tests/test_realtime_simulation.py tests/test_transcribe_sherpa_onnx.py -q
```

当前结果：

```text
25 passed, 1 warning
```

关键结论：

- sherpa-onnx 已能产出真实 streaming event JSON，并接入 scheduler。
- 25 个 partial 没有触发 LLM，成本边界符合预期。
- `end_of_stream` 当前被 scheduler 视为控制事件并忽略，这是合理降级；后续可进入质量状态事件。
- 当前短样本仍只有 1 条 final segment，不满足“多条 chunk final segment”目标，不能宣称真实 streaming final 切分达标。
- sherpa-onnx 性能很好，但中文技术词质量仍不足，下一步应优先验证 FunASR streaming 或调 sherpa endpoint/模型参数。

### 6.15 FunASR streaming event adapter smoke

本轮新增：

```text
meeting-copilot/code/asr_runtime/tests/test_transcribe_funasr.py
```

`transcribe_funasr.py` 新增能力：

- 保留原文件转写 `transcribe(...)`，不破坏既有 FunASR 文件链路。
- 新增 `stream_events(...)`，用 FunASR streaming 参数把本地音频文件切成 chunk 回放，输出统一 `StreamingTranscriptEvent`。
- 新增 `transcribe_streaming(...)`，把 streaming events 转成统一 provider transcript JSON。
- CLI 支持 `--streaming`、`--chunk-size`、`--encoder-chunk-look-back`、`--decoder-chunk-look-back`、`--final-window-ms`、`--events-output`。
- provider stdout 噪声继续重定向到 stderr，stdout 只输出 JSON。
- provider JSON 的 `raw` 只记录 `model_id`、chunk 参数、事件计数等，不记录本地音频路径。

测试命令：

```bash
cd <repo>/code/asr_runtime
pytest tests/test_transcribe_funasr.py -q
```

结果：

```text
5 passed, 1 warning
```

真实模型 smoke 命令：

```bash
cd <repo>/code/asr_runtime
.venv-funasr/bin/python scripts/transcribe_funasr.py \
  outputs/simulated-release-review.16k.wav \
  --streaming \
  --model paraformer-zh-streaming \
  --local-model-dir <absolute-local-funasr-model-dir> \
  --device cpu \
  --chunk-size 0,10,5 \
  --encoder-chunk-look-back 4 \
  --decoder-chunk-look-back 1 \
  --final-window-ms 3000 \
  --events-output outputs/simulated-release-review.funasr.streaming-events.json \
  > outputs/simulated-release-review.funasr.streaming-provider.json
```

首次运行观察：

- FunASR 下载 ModelScope online streaming 模型 `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`。
- `model.pt` 约 840MB。
- 2026-07-03 后续修正：上述历史命令已被 offline guard 收紧，当前必须显式传入本地模型目录；缺失时 blocked，不再允许裸跑 alias 触发下载。
- 这是本地免费模型下载，不是远程 ASR 付费调用。
- 但它会带来明显首次启动、磁盘占用和打包发布成本。

warm run 结果：

```json
{
  "text": "我们这次mentate为先灰度百分之十如果错误率超过百分之零点一九回滚这里还没有确认回滚负责人张三下周三补充兼容性测试用例监控指标还需要确认t九九核错误率",
  "latency_ms": 18864,
  "audio_duration_seconds": 17.929,
  "rtf": 1.05215,
  "finalization_strategy": "fixed_window_from_partial_hypotheses",
  "provider_endpoint_finals": false,
  "partial_event_count": 30,
  "final_event_count": 6,
  "end_of_stream_event_count": 1,
  "segment_count": 6
}
```

segment 示例：

```text
funasr_001 0-3000     我们这次mentate为先灰度百
funasr_002 3000-6000  分之十如果错误率超过百分
funasr_003 6000-9000  之零点一九回滚这里还没有确
funasr_004 9000-12000 认回滚负责人张三下周三补
funasr_005 12000-15000 充兼容性测试用例监控指标
funasr_006 15000-17929 还需要确认t九九核错误率
```

接入 scheduler：

```bash
python3 scripts/realtime_simulation.py \
  --events-json outputs/simulated-release-review.funasr.streaming-events.warm.json \
  --provider funasr \
  --min-final-interval-ms 30000 \
  --min-state-change-interval-ms 10000 \
  --max-calls-per-hour 80
```

调度结果：

```json
{
  "llm_trigger_count": 1,
  "decision_reasons": {
    "partial_ignored": 30,
    "final_segment": 1,
    "cooldown": 5,
    "control_event_ignored": 1
  }
}
```

关键结论：

- FunASR streaming 文件回放已经实现多条窗口化 final segment，满足当前 `REQ-ASR-004` 的文件回放 contract smoke 目标；但 final 语义仍是适配层窗口切分，不等于 provider endpoint/final 语义达标。
- scheduler 正确忽略 30 个 partial，没有按 partial 调 LLM。
- 6 条 final 中只有第 1 条触发 LLM，其余受 cooldown 限制，符合成本边界。
- warm run RTF 约 1.05，接近实时边界但仍偏重；后续要继续调 chunk 参数、模型选择和 worker 架构。
- 中文语义总体可读，但技术实体仍不达标：`payment-gateway` 被识别成近似英文碎片，`P99` 被识别为 `t九九`，`0.1%` 识别为中文数字片段。
- 因此 FunASR streaming 可以进入下一轮热词/术语表/normalizer bake-off，但不能直接宣布中文技术会议质量达标。
- 本结果仍是文件回放式 streaming，不是 macOS 桌面实时音频采集。
