# ASR Provider 策略与成本边界

> 日期：2026-06-18  
> 结论：默认不增加远程 ASR 付费项。MVP 优先走本地/开源 ASR；远程中文 ASR 只作为评测对照、可选高质量模式或企业自选 provider。

## 1. 核心判断

本产品需要两类模型能力：

```text
音频 -> 文字：ASR
文字 -> 诊断 / 建议 / 总结 / 状态机：LLM
```

默认成本策略：

- ASR 默认走本地开源方案，不按分钟/小时产生第三方 API 费用。
- LLM 默认走用户配置的 OpenAI-compatible 中转站，这是主要远程模型成本。
- 远程 ASR 不作为 MVP 默认依赖。
- 远程 ASR 只作为 bake-off 对照、企业可选高质量模式、或本地 ASR 不达标时的备选。

也就是说，默认链路应是：

```text
会议音频
  -> 本地 ASR
  -> 实时文字
  -> 转写稳定器
  -> LLM 中转站
  -> 会议状态机 / 缺口雷达 / 建议卡片 / 会后纪要
```

不是：

```text
会议音频
  -> 付费云 ASR
  -> 付费 LLM
```

后者只能作为可选高质量链路，不能作为默认产品形态。

## 2. 为什么还要比较远程 ASR

远程 ASR 比较不是为了引入额外收费，而是为了做质量参照。

原因：

- 如果本地 ASR 达标，就不需要默认云 ASR。
- 如果本地 ASR 不达标，但远程中文 ASR 达标，说明产品方向成立，但成本/隐私/定价需要重新评估。
- 如果远程中文 ASR 也不达标，实时 Copilot 风险很高，应降级为会后结构化纪要。

远程 ASR 在本项目中的定位：

```text
bake-off 对照尺子
  不是默认生产依赖
  不是强制收费项
  不是第一阶段必选实现
  不阻塞 Mac MVP 的本地默认链路
```

## 3. 开源项目能力判断

### Meetily

定位：

- 开源会议助手 / 桌面客户端候选基座。
- 本地录音、本地实时转写、本地保存、会议总结。
- 更适合作为客户端、音频采集、实时转写 UI、会话管理的参考或二次开发基座。

适合复用：

- 桌面端结构。
- 麦克风和系统音频采集。
- 实时 transcript 展示。
- 本地会议存储。
- OpenAI-compatible / Ollama 等 AI provider 配置思路。

不应直接假设：

- 中文技术会议 ASR 已经达标。
- 技术词、接口名、字段名、中英混排能满足本项目要求。
- 缺口雷达、会议状态机、建议卡片已经存在。

结论：

- Meetily 可作为“桌面会议助手壳和本地录音转写链路”的候选轮子。
- 中文技术 ASR 和 Copilot 智能层需要我们替换或增强。

### FunASR / Paraformer

定位：

- 中文质量优先候选，但不是未经评测的默认结论。
- 支持流式/实时能力。
- 可本地部署，也可自建服务。

适合承担：

- MVP 默认本地 ASR 第一候选。
- 中文技术会议 ASR bake-off 第一优先接入。
- 远程云 ASR 之前的主要低成本基线。

风险：

- 本地 CPU 是否能稳定实时，需要实测。
- 热词、标点、时间戳、长会议稳定性需要 bake-off 验证。
- 不同模型大小对硬件要求不同。

当前本机实测：

- `funasr==1.3.10` 已在独立 `.venv-funasr` 中可运行。
- venv 约 1.2GB。
- Paraformer ASR 模型缓存约 954MB。
- VAD 模型缓存约 3.9MB。
- `ct-punc` 标点模型缓存约 1.1GB。
- CPU no-punc 模式：17.9 秒合成技术会议样本耗时约 6.3-7.0 秒，RTF 约 0.35-0.39，峰值内存约 3.5GB。
- CPU punc 模式：17.9 秒样本耗时约 17 秒，RTF 约 0.95，峰值内存约 5.8GB。
- no-punc 输出带 timestamp，可转换为统一 `segments`，能进入 EvidenceSpan 链路。
- 文件模式默认返回整段或粗粒度结果，不等于真实 streaming final segment。
- `paraformer-zh-streaming` 文件回放式 streaming smoke：17.9 秒合成技术会议样本输出 30 个 partial、6 个 final、1 个 end_of_stream；warm run latency 约 18.8 秒，RTF 约 1.05。
- 首次运行会下载约 840MB online streaming 模型；这不是云 ASR 付费调用，但会带来首次启动、磁盘和打包成本。
- streaming smoke 能接入统一 `StreamingTranscriptEvent` 和 scheduler，30 个 partial 全部被忽略，6 个 final 中只有 1 次触发 LLM，其余受 cooldown 限制。
- 当前 6 个 final 的语义是 `fixed_window_from_partial_hypotheses`，不是 FunASR provider endpoint final；它用于文件回放 contract smoke 和调度验证，不能直接作为生产证据语义。
- 技术词仍有错误：`payment-gateway` 识别近似为 `payment gate 为`，`P99` 识别为 `t 九九`。
- 文件模式输出的 final segment 必须标记为文件/伪实时结果；不能把它包装成真实 streaming 达标。
- streaming 文件回放输出多条 final segment，但仍不等于 macOS 桌面实时音频采集达标。

结论：

- FunASR 是中文质量路线的必要候选，但依赖和模型体积较重，不能静默下载或打进轻量默认包。
- MVP 可提供 FunASR 本地 provider，但应作为“质量模式/本地大模型模式”，并明确磁盘和内存要求。
- FunASR streaming 已证明可以产出多条窗口化 final segment 并接入 LLM 调度器；下一步重点转为 provider endpoint/final 语义、热词、技术实体、chunk 参数、长会议稳定性和桌面采集延迟。
- `ct-punc` 不适合作为默认实时首轮必开项；首轮应先走 no-punc + normalizer/stabilizer。
- FunASR streaming 必须和 sherpa-onnx streaming 共用同一输出契约，不能各自写特例。
- no-punc + normalizer 可以作为实时首轮候选，但 EvidenceSpan quote 仍应保留 raw transcript，normalized 文本只作为辅助字段。

### sherpa-onnx

定位：

- Mac 端侧可行性首测候选。
- 支持实时/流式模型，包含中文和中英混合模型。
- 适合桌面端、边缘端、多平台部署探索。

适合承担：

- 本地低成本 ASR 第二候选。
- Windows/macOS 端侧部署评测。
- 弱网/离线模式。

风险：

- 中文技术词准确率、热词能力和标点效果要实测。
- 模型选择、打包体积和端侧性能要评估。

当前本机实测：

- `sherpa-onnx==1.13.3` 在 Python 3.11 隔离环境可运行。
- 中文 int8 模型目录约 26MB，venv 约 141MB。
- 352.6 秒真实中文录音转写耗时约 4.1 秒，RTF 约 0.0115。
- 17.9 秒合成技术会议短样本转写耗时约 0.46 秒，RTF 约 0.0257。
- streaming event adapter smoke：17.9 秒合成技术会议样本输出 25 个 partial、1 个 final、1 个 end_of_stream，端到端 latency 约 556ms，RTF 约 0.031。
- 端侧性能足以支撑实时方向。
- 技术词存在明显错误：`payment-gateway` 丢失，`灰度` 识别为近音词，`P99` 识别为“九九”。

结论：

- sherpa-onnx 可作为 Mac MVP 的端侧性能基线。
- sherpa-onnx 当前模型不能直接作为中文技术会议质量最终方案。
- sherpa-onnx 已能接入统一 streaming event contract 和 scheduler，但当前短样本仍只有 1 条 final segment，chunk final 切分不达标。
- 下一步必须验证 FunASR streaming、热词/术语表、数字指标归一化和 LLM second-pass 修正。

### Transcript normalizer / stabilizer

定位：

- 位于 ASR 和 LLM 之间，是默认链路必选层。
- 保留 raw transcript，同时生成 `normalized_text` 和 `normalization_changes`。
- 处理字间空格、百分比、指标名、服务名、中英混排等低风险修正。

当前实测：

- FunASR no-punc raw text 在 `S02-release-review` 的核心技术实体召回为 0.0。
- 加入技术术语表和数字归一化后，normalized 技术实体召回为 0.75。
- LLM 在 normalized context 下生成 3 张建议卡片、10 个状态事件，demo gate 通过。

边界：

- normalizer 不能覆盖 raw text，不能把 raw ASR 质量假装成达标。
- normalizer 只能做可解释修正，必须记录 alias -> canonical。
- 低置信度或无依据的修正只能进入待确认，不得写入正式证据 quote。

### SenseVoice

定位：

- 多语种语音理解/ASR 候选。
- 适合补充中英混合、多语种、情绪/音频事件等能力探索。

适合承担：

- ASR bake-off 候选。
- 中英混排和多语种会议样本对照。

风险：

- 官方主模型与流式实时链路的成熟度需要实测。
- 不应在未评测前作为 MVP 默认主链路。

## 4. 推荐实现方案

### 阶段 1：先复用开源能力，打通本地实时转写

目标：

- 让桌面端实时显示中文文字。
- 不增加云 ASR 成本。
- 先证明“会议中实时文字流”可用。

方案：

```text
桌面客户端 / Meetily 候选基座
  -> mic/system audio capture
  -> local ASR adapter
      -> FunASR streaming
      -> sherpa-onnx streaming
      -> mock / command provider
  -> realtime transcript panel
  -> local session storage
```

首选：

- 质量优先：FunASR streaming / Paraformer 中文流式模型。
- 端侧首测：sherpa-onnx 中文 int8 streaming 模型。

备选：

- SenseVoice second-pass 或中英混排补充模型。

不做：

- 不从零手写 ASR。
- 不默认接阿里/讯飞/腾讯/百度云 ASR。

mock / command provider 边界：

- `mock streaming provider` 用来可控地产生 `partial/final/revision` 序列，验证 stabilizer、EvidenceSpan、scheduler、gap rules 和 LLM 调用节流。
- mock streaming 不是 ASR 质量评测工具，报告必须标记为契约测试，不能计入 provider 质量排名。
- `command provider` 可用于离线或伪实时接入已有脚本，但不能替代 streaming event contract。

### 阶段 2：在实时文字基础上接 LLM 诊断和建议

目标：

- 不是只显示文字，而是从稳定 transcript 生成会议状态和建议。

链路：

```text
ASR partial/final/revision
  -> transcript stabilizer
  -> technical term normalizer
  -> meeting state engine
  -> LLM gateway
  -> suggestion cards
  -> post-meeting summary
```

LLM 负责：

- 抽取候选决策、行动项、风险、未闭环问题。
- 生成建议卡片文案。
- 生成会后纪要。
- 保守修正转写可读性。

LLM 不负责：

- 发明缺失信息。
- 自动裁决技术方案。
- 自动补 owner、deadline、阈值。

### 阶段 3：远程 ASR 仅作为对照或可选增强

触发条件：

- 本地 ASR 无法达到中文技术实体指标。
- 用户明确选择高质量云 ASR 模式。
- 企业已有云 ASR 采购或私有 ASR 服务。

实现方式：

```text
ASRProvider
  local_funasr      默认候选
  local_sherpa      默认候选
  local_sensevoice  实验候选
  custom_command    通用外部命令
  remote_aliyun     可选
  remote_iflytek    可选
  remote_tencent    可选
  remote_baidu      可选
```

配置原则：

- 默认配置不启用 remote provider。
- remote provider 必须明确显示可能产生额外费用。
- API key 不写入仓库。
- 评测报告要标记 provider 类型：local / self-hosted / remote-paid。

## 5. 为什么不是自己实现 ASR

不建议自己从零实现 ASR。

原因：

- ASR 是独立模型系统，不是普通业务代码。
- 中文实时、噪声、多说话人、标点、热词、中英混排都很复杂。
- 从零实现成本高，效果大概率不如成熟开源方案。

我们应该自己实现的是：

- ASR provider 抽象。
- 音频到 ASR 的实时管线。
- 转写稳定器。
- 技术词归一化。
- 会议状态机。
- 缺口雷达。
- 建议卡片。
- 评测与质量闭环。

也就是说：

```text
ASR 模型能力：复用开源/可选 provider
Copilot 产品能力：自己实现
```

## 6. 当前默认决策

- 第一阶段不直接把 Meetily 作为唯一基座；先复用其客户端/采集/会话管理思路，核心 Copilot 智能层自建。
- MVP 首发平台为 macOS Apple Silicon；Windows later。
- ASR 评测优先级与产品默认候选分开：sherpa-onnx 是端侧性能基线，FunASR 是中文质量候选，SenseVoice 是实验补充。
- 允许 MVP 要求用户下载模型文件，但必须显式展示磁盘和内存成本，不能静默下载 GB 级模型。
- CPU-only 必须可运行；GPU/MPS 只能作为性能增强，不作为首版必要条件。
- 会议音频默认本地保存，用户可关闭保存或会后删除。
- LLM 中转站使用 OpenAI-compatible 协议，必须记录调用次数、模型、prompt version、失败/重试；usage 成本记录是下一轮必做项。

## 7. 文档约束

后续需求、架构和代码实现不得违背以下结论：

- 不把远程 ASR 作为默认必选链路。
- 不在没有 bake-off 的情况下承诺中文实时识别质量。
- 不把开源会议助手的实时转写能力等同于中文技术会议 Copilot 能力。
- 不把 ASR 转写当作产品终点；实时建议和证据化状态机才是差异点。
- 不新增隐藏收费项；任何付费 provider 都必须在配置和文档中显式说明。
