# Meeting Copilot 项目阶段进度报告

> 日期：2026-07-02  
> 范围：当前仓库 `/Users/chase/Documents/面试/meeting-copilot` 的 PC 端开发进度、最新验证结果、风险边界和下一步计划。  
> 约束：本报告未读取 `configs/local/`，未读取真实用户音频，未调用远程 ASR/LLM，未安装 Rust，未运行 `cargo`、Tauri 或 package manager。

## 1. 总体结论

当前项目已经从“需求和 ASR 可行性评估”推进到“PC Local Web MVP + Mac-first 桌面端安全边界”阶段。

最重要的结论是：

- 产品价值方向已经固定：Meeting Copilot 不是音频转文字工具，而是中文技术会议中的实时/准实时工程 Copilot。
- 当前不是在无限 ASR 测评循环里。ASR bake-off 已经降为门禁和回归工具，主线已经转向 PC 客户端落地。
- Core、Web MVP、Live ASR synthetic event、no-LLM scheduler、LLM request preview、provider config boundary、card lifecycle dry-run 等链路已经形成可测骨架。
- 桌面端已经有 Tauri 静态 scaffold 和 no-op native bridge，但还没有运行真实 Tauri shell、没有接真实音频采集、没有启动 ASR worker。
- `PCWEB-088` 的代码评审后安全加固红灯已经修复：Rust 人工安装后的 post-install probe 审批包会大小写不敏感地拦截 forbidden roots，对 `forbidden_default_side_effects` 做 canonical validation，并防止 invalid custom policy 控制报告顶层身份字段。
- `PCWEB-089` 已完成 no-command Rust post-install probe result intake：只接受 caller-provided bounded JSON status，不执行 probe，不解锁 cargo check，并已修复评审发现的 raw/path/secret-like error echo 与 failed result 污染 normalized result 风险。
- 在没有显式授权前，后续仍不能安装 Rust、运行 `rustc/cargo/rustup/xcode-select`、运行 `cargo check`、读取密钥或接入真实远程调用。

一句话进度：

```text
需求/架构/评测基线已完成
PC Web MVP 已有可运行本地骨架
Live ASR/LLM/card lifecycle 已有 no-cost dry-run 链路
Tauri 桌面壳已有静态 scaffold
当前卡点已经从 PCWEB-089 result intake 收口转为下一桌面增量选择
真实麦克风/系统音频/ASR worker/LLM enabled executor 还没进入实现
```

## 2. 产品价值判断

这个项目仍然有继续做的价值，但价值不在转写本身。

已确认的差异化点：

- 会中维护结构化状态：`DecisionCandidate`、`ActionItem`、`Risk`、`OpenQuestion`。
- 只在工程语境下生成工程建议，非工程会议工程卡片必须为 0。
- 所有正式状态、建议和纪要都必须能回到 `EvidenceSpan`。
- 建议卡片不是泛泛总结，而是检查 owner、deadline、rollback、test/verification、metric/monitoring 等工程缺口。
- LLM 不能直接从 ASR partial 触发强建议，必须经过 final/revision、状态变化、调度器和证据 gate。
- 低质量 ASR、LLM 超时、schema 失败、证据链不完整时必须降级，不输出强建议。

仍未证明的核心价值：

- 真实会议音频进入桌面客户端后的实时 ASR 延迟和准确性。
- 真实 ASR final/revision 与状态机、建议卡片之间的端到端时延。
- 真实 LLM enabled executor 的稳定性、成本和 schema 通过率。
- 用户在真实会议中是否愿意保留建议卡片，而不是觉得打扰。

因此当前阶段不应该继续扩大评测范围，而应该把可运行 PC 客户端路径打通，再用真实会议流验证价值。

## 3. 当前已完成内容

### 3.1 文档和决策体系

已经建立的核心文档：

- `docs/product-requirements.md`：产品定位和 MVP 需求。
- `docs/implementation-roadmap.md`：SDD/TDD 实施路线。
- `docs/requirements-traceability-matrix.md`：需求、验收、测试追踪矩阵。
- `docs/decision-log.md`：重要产品、架构、成本、隐私和阶段决策。
- `docs/privacy-and-data-flow.md`：隐私、安全和数据流边界。
- `docs/platform-packaging-and-store-compliance.md`：PC 跨平台、打包、签名、移动端上架预研。
- `docs/pc-local-web-mvp-acceptance.md`：PC Local Web MVP 验收清单。
- `docs/project-structure.md`：项目目录和职责划分。

核心决策已经记录：

- Mac-first，Windows later。
- 默认本地 ASR，远程 ASR 只作为显式可选或评测对照。
- LLM 使用 OpenAI-compatible 中转站协议，但密钥只允许在本地私有配置中。
- 核心智能层自研，底层能力复用开源库，桌面壳择机二开或自建。
- PC 端采用共享 core/UI + 分平台 adapter + 分平台打包，不是两套业务代码。

### 3.2 ASR 和评测

已有内容：

- 中文技术会议 ASR 评测集目录和 manifest。
- mock provider 和 command provider。
- 中文 CER、技术实体召回、延迟指标。
- FunASR/sherpa-onnx 的本地文件链路和输出报告设计。
- `StreamingTranscriptEvent` 风格的 partial/final/revision/error/end_of_stream 契约。
- synthetic Live ASR event JSON 可以进入 Web MVP 的 live envelope。

当前边界：

- mock 和 synthetic 只能证明契约、UI 和状态链路，不证明真实麦克风、系统音频或真实 ASR endpoint final 质量。
- 后续 ASR 不再作为无限 bake-off 主线，只在真实桌面音频链路接入后做 targeted 验证。

### 3.3 Core 层

已有内容：

- 平台无关的 core，不依赖 Tauri/Electron/macOS/Windows API。
- EvidenceSpan、TranscriptSegment、SuggestionCard、StateEvent 等合同。
- 会议快照聚合。
- 正式状态和建议卡片必须带证据的 gate。
- 非工程会议工程建议卡为 0 的 gate。
- 实时建议窗口、降级、schema failure/timeout/invalid 静默路径。
- 建议卡片状态：保留、忽略、标记错误、too_late、too_intrusive。
- JSON/Markdown 报告导出模型。

价值：

- 已经从底层约束上防止产品退化成“转写 + 总结”。
- Web、桌面、后续 Windows adapter 都应复用这一层。

### 3.4 PC Local Web MVP

已有内容：

- FastAPI 本地 backend。
- 静态 Web 工作台。
- fixture session 创建、读取、删除。
- demo fixture evaluation summary。
- replay event stream。
- Live Mock SSE。
- Live ASR synthetic SSE。
- EventSource 增量 UI。
- Evidence click-back。
- 本地 JSON session persistence。
- Live ASR draft review。
- no-LLM scheduler decision log。
- no-LLM suggestion candidate queue。
- no-LLM LLM request draft queue。
- OpenAI-compatible request body preview。
- request body redaction guard。
- provider config validation/boundary/masked status dry-run。
- provider secret storage policy。
- card creation policy dry-run。
- card lifecycle preview、append preflight、disabled run、repository dry-run、transaction disabled run、audit preview、retry/replay preflight、serializer dry-run、mutation preflight、commit preflight、idempotency preflight、audit persistence preflight、readiness summary。

当前可演示的内容：

- 用 fixture 或 synthetic Live ASR event 展示 transcript、EvidenceSpan、状态候选、scheduler trace、候选建议、request draft 和 readiness panels。
- 可以证明 UI 和 API 能消费 live envelope。

仍未完成：

- 真实桌面音频采集。
- 真实 ASR worker 的实时输入。
- 真实 LLM executor enabled run。
- 正式建议卡片的真实生命周期写入。

### 3.5 桌面端路径

已完成的 PCWEB 桌面路径：

- `PCWEB-079`：desktop shell readiness boundary。
- `PCWEB-080`：desktop runtime boundary，推荐 `tauri_first_electron_fallback`。
- `PCWEB-081`：native bridge command contract。
- `PCWEB-082`：Tauri v2 静态 scaffold。
- `PCWEB-083`：desktop build readiness policy。
- `PCWEB-084`：cargo check artifact policy。
- `PCWEB-085`：Rust toolchain readiness policy。
- `PCWEB-086`：Rust toolchain installation decision policy。
- `PCWEB-087`：Rust toolchain install approval packet。
- `PCWEB-088`：Rust post-install probe approval packet，评审加固失败项已修复，仍保持 no-probe/no-cargo/no-secret/no-audio 边界。

桌面端当前事实：

- 已有 `code/desktop_tauri/src-tauri` 静态源码 scaffold。
- 已有 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个 no-op command。
- 当前没有运行 Tauri、没有构建、没有打包、没有音频权限、没有真实 IPC 动作。

## 4. 最新验证结果

本报告生成前运行了以下命令。

### 4.1 PCWEB-088 post-review focused test

命令：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q
```

结果：

```text
8 passed, 1 warning
```

修复前 RED 证据：

```text
2 failed, 6 passed, 1 warning
```

当时失败 1：

```text
test_custom_policy_cannot_add_probe_commands_relax_flags_or_remove_redaction
```

原因：

- 新增测试要求 custom policy 不能移除 `forbidden_default_side_effects`。
- 当时 tool 还没有把 `forbidden_default_side_effects` 固定成 canonical constant 并做 exact equality validation。

已修复：

- 在 `tools/desktop_rust_post_install_probe_approval.py` 中增加 canonical `FORBIDDEN_DEFAULT_SIDE_EFFECTS`。
- 校验 custom policy 的 `forbidden_default_side_effects` 必须完全匹配 PCWEB-088 禁止副作用清单。
- validation 失败时返回可信 canonical list，而不是回显 custom policy 的空列表或恶意列表。

当时失败 2：

```text
test_custom_policy_path_rejects_mixed_case_forbidden_roots_before_reading
```

原因：

- 当前 forbidden path guard 对路径 parts 做大小写敏感匹配。
- 在 macOS 风格大小写不敏感文件系统语义下，`CONFIGS/LOCAL` 可能指向 `configs/local`，但当前 guard 未在读文件前拦截。

已修复：

- forbidden root path suffix matching 应对每个 path component 做 `casefold()` 后比较。
- in-repo 和 outside-repo 的 mixed-case forbidden roots 都必须在读文件前返回 blocked report，不能触发 `FileNotFoundError`。
- 复审还发现 invalid custom policy 可控制顶层 `pcweb_id/policy_name/policy_status`。已补 RED 用例并修复为工具侧 canonical constants，修复后 `python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q` 仍为 `8 passed`。

### 4.2 PCWEB-088 combined and desktop boundary tests

命令：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q
```

结果：

```text
15 passed, 1 warning
```

命令：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 -m pytest tests/test_desktop_rust_toolchain_install_approval_packet.py tests/test_desktop_rust_toolchain_installation_decision.py tests/test_desktop_rust_toolchain_readiness.py tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_build_readiness_policy.py tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q
```

结果：

```text
61 passed, 1 warning
```

说明：

- 当前 quality gate profile 合同仍通过。
- 默认 profile 不应运行 Rust、Cargo、Tauri、package manager、远程 provider 或读取 `configs/local/`。
- PCWEB-088 修复没有打开 post-install probe、Cargo check、Tauri、音频、worker、密钥或远程 provider。

### 4.3 Full local gates

命令：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
```

结果：

```text
root 72 passed
core 34 passed
web backend 300 passed
browser smoke status ok
quality gate profile=pc-web passed
```

命令：

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

结果：

```text
ASR runtime 65 passed
ASR bake-off 18 passed
root 72 passed
core 34 passed
web backend 300 passed
quality gate profile=all-local passed
```

说明：

- 这两个 gate 仍是本地免费验证。
- 没有调用 LLM 中转站、远程 ASR 或 `configs/local/`。

### 4.4 Secret scan

命令：

```bash
rg -n --hidden \
  --glob '!configs/local/**' \
  --glob '!**/.venv/**' \
  --glob '!**/__pycache__/**' \
  --glob '!**/.pytest_cache/**' \
  --glob '!*.pyc' \
  'sk-[A-Za-z0-9]{20,}' .
```

结果：

```text
no matches
```

说明：

- 排除 `configs/local/**` 后，仓库未检出 `sk-...` 形态密钥。

## 5. 当前红灯和风险

### 5.1 当前红灯状态

`PCWEB-088` 评审加固失败项已经修复：

- forbidden root path matching 已改为大小写不敏感。
- `forbidden_default_side_effects` 已改为工具侧 canonical validation。
- invalid custom policy 顶层 `pcweb_id/policy_name/policy_status` 已改为工具侧 canonical 输出。

这些问题属于安全边界问题。它们不代表 Web MVP 业务链路坏掉，但会影响进入下一步桌面工具链探针前的可信度。当前 focused、desktop boundary、`pc-web` 和 `all-local --no-browser` 回归已经通过，hygiene 扫描干净，PCWEB-090 评审发现的 delegated validation error 泄漏风险也已修复并补了回归测试。

### 5.1.1 PCWEB-089 result intake 已完成

`PCWEB-089` 已新增 `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` 和 `tools/desktop_rust_post_install_probe_result_intake.py`，用于 no-command Rust post-install probe result intake。该边界只接受 `caller_provided_json_only` 的 bounded status，报告模式为 `manual_result_validation_only`，拒绝 raw stdout/stderr、command、path、env、home/cache、provider config、api_key、authorization、bearer token 等字段，并保持 `safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false` 和 `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`。

验证结果：

```text
PCWEB-089 RED: 10 failed, 1 warning
PCWEB-089 focused GREEN: 11 passed, 1 warning
PCWEB-089 + PCWEB-088 + quality gate tests: 26 passed, 1 warning
desktop/root regression: 72 passed, 1 warning
docs gate: 1 passed, 2 warnings
pc-web quality gate: root 72 passed, core 34 passed, web backend 300 passed, browser smoke status ok
all-local --no-browser: ASR runtime 65 passed, ASR bake-off 18 passed, root 72 passed, core 34 passed, web backend 300 passed
```

`PCWEB-089` 仍不执行 Rust probe、不运行 cargo check、不运行 Tauri、不读取 raw probe output、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。真实 probe execution 仍必须另起显式授权增量。

评审后补强结果：

- invalid enum error 不再回显调用方提供的 raw stdout、路径或 bearer-like 字符串。
- 任意 result validation failed 都返回默认安全的 `normalized_probe_result`。
- policy validation 已覆盖 `next_required_decisions` 和 `cargo_check_blockers`，避免 policy drift 静默放宽边界。
- 当前状态/进度文档已纳入 docs gate，避免后续报告继续引用 PCWEB-089 完成前的旧口径。

### 5.2 产品风险

- 真实会议音频下的中文技术 ASR 准确率仍是最大风险。
- 实时性是否达标还需要真实 audio chunk -> ASR final -> state -> candidate/card 的端到端计时。
- 过多 dry-run boundary 会让开发看起来慢，但它们是在保护密钥、费用、本地文件和系统命令边界。
- 如果后续只把 Live ASR synthetic event 做得很完善，而不尽快接真实桌面音频，产品价值仍然没有被证明。

### 5.3 工程风险

- Tauri/Rust 还没有运行过真实 `cargo check`。
- 当前仓库没有确认 Rust toolchain 环境，也不能默认安装。
- 真实 macOS 系统音频采集需要处理权限、设备、系统版本和会议软件差异。
- ASR worker 和模型分发会带来模型许可证、缓存、首次下载、包体大小和清理问题。
- LLM enabled executor 要严格控制频率、schema、重试、成本和密钥读取边界。

## 6. 后续计划

### 6.1 立即下一步：PCWEB-090 之后的桌面路线

目标：

- `PCWEB-089` post-install probe result intake boundary 已完成：只接受 caller-provided bounded JSON status，不执行 `rustc/cargo/rustup/xcode-select`，不读取 raw output/path/env/home/cache，不解锁 cargo check。
- 如果后续要执行真实 Rust probe，必须另起新的显式授权增量；不能把 `PCWEB-089` 当作 probe execution boundary。
- 如果仍不允许继续靠近 Rust/Tauri 运行，则继续做平台无关的 ASR worker/audio adapter/session lifecycle contract。
- 不管走哪条路线，仍保持不读取密钥、不读真实音频、不调用远端、不生成非预期 artifacts 的边界。

推荐执行顺序：

```text
1. 先写 PCWEB-091 或替代路线计划
2. 写 RED tests
3. 最小实现
4. focused gate
5. pc-web / all-local gate
6. 文档和决策日志回写
```

### 6.2 PC 端下一阶段：从静态 scaffold 进入可运行 shell

前置条件：

- PCWEB-088 全绿。
- 用户确认 Rust 已由用户在 repo 外人工安装，或者明确允许进入 Rust 工具链探针。
- 仍需遵守不读取密钥、不读真实音频、不调用远端、不生成非预期 artifacts 的边界。

建议路径：

```text
PCWEB-089
  completed: post-install probe result intake boundary
  只接受 caller-provided bounded JSON status
  不运行 rustc/cargo/rustup/xcode-select，不解锁 cargo check

PCWEB-090
  completed as explicit_manual_execution_packet_only
  使用 PCWEB-084 的 Cargo.lock / CARGO_TARGET_DIR / network / cleanup policy
  只生成 ready_for_explicit_user_approval 手动执行包，不运行 cargo check

PCWEB-091
  Tauri no-op shell local run smoke
  只验证 no-op command 和 Web MVP 加载

PCWEB-092
  desktop native bridge no-op integration tests
  继续不接音频，只验证 IPC 合同
```

如果没有 Rust 执行授权，则替代路线是继续做平台无关代码：

- ASR worker process contract。
- audio capture adapter contract。
- desktop session lifecycle contract。
- 本地 Web MVP 与 future desktop shell 的 API 兼容测试。

### 6.3 真实音频链路

Mac-first 后续应做：

- 麦克风权限状态读取。
- 麦克风设备枚举。
- 手动开始/暂停/停止录音。
- 系统音频采集技术 spike。
- 音频 chunk 写入临时目录和清理策略。
- ASR worker sidecar 输入输出协议。
- 音频质量事件：静音、过载、丢帧、设备切换。

边界：

- 不做隐蔽录音。
- 不默认自动启动录音。
- 不把真实音频提交到仓库。
- 不默认远程 ASR。
- 音频生命周期必须能删除。

### 6.4 真实 LLM enabled executor

当前已有大量 no-LLM/dry-run 链路，后续 enabled executor 应在真实音频链路初步可用后再开启。

建议前置条件：

- provider config loader 已有明确授权。
- secret reference 不回显、不 fingerprint、不推断 key presence。
- OpenAI-compatible request body preview 和 schema outline 已稳定。
- candidate/card lifecycle dry-run 全绿。
- 每次请求记录 trigger reason、segment batch、prompt version、model、usage、schema result、show/silence decision。
- 有成本预算和 cooldown。

## 7. 后续不应该继续做什么

为了避免再次陷入“永远评测”的循环，以下事情暂时不作为主线：

- 不继续扩大远程 ASR provider bake-off，除非本地 ASR 在真实音频链路上明显不可用。
- 不继续做更多 fixture-only 的 UI 细节，除非它阻塞真实桌面链路。
- 不直接启用 LLM 付费调用，直到真实 audio -> ASR -> state/candidate 链路能稳定产生候选。
- 不先做 Windows 音频采集，Mac MVP 跑通后再扩展。
- 不做移动端上架实现，移动端只保留规划文档。
- 不 fork 大型开源会议转写项目作为主仓库，除非它能明确降低桌面音频/ASR worker 实现成本且不破坏 core-first 架构。

## 8. 当前推荐判断

我建议继续推进，不建议暂停项目。

理由：

- 产品价值和差异化已经比早期清晰。
- 当前代码不是简单转写工具，已经有状态机、证据链、scheduler、candidate、card lifecycle 和降级边界。
- PC Web MVP 和 synthetic Live ASR 已经证明核心事件链路可以被 UI 消费。
- 最近的 PCWEB-088/PCWEB-089 红灯都是具体、已修复的安全加固问题，不是方向性失败。

但也不建议继续在文档和 dry-run 上无限扩展。PCWEB-090 收口后，应尽快进入“可运行桌面壳”和“真实音频链路”。

推荐下一阶段成功标准：

```text
1. Tauri no-op shell 能本地运行并加载 Web MVP
2. no-op native commands 可通过 IPC 返回状态
3. Mac 麦克风输入能产生本地音频 chunk
4. ASR worker 能从 chunk 输出 final/revision
5. Web 工作台能从真实 ASR event 增量展示状态候选
6. 仍保持默认 0 远程 ASR 费用
7. LLM 真实调用仍在显式 enabled executor 后开启
```

## 9. 可追溯清单

本报告对应的主要文件：

- `README.md`
- `docs/product-requirements.md`
- `docs/implementation-roadmap.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/project-structure.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/platform-packaging-and-store-compliance.md`
- `docs/pcweb-088-desktop-rust-post-install-probe-approval-plan.md`
- `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`
- `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`
- `tests/test_desktop_rust_post_install_probe_approval.py`
- `tests/test_desktop_rust_post_install_probe_result_intake.py`
- `tests/test_desktop_first_cargo_check_execution_boundary.py`
- `tests/test_quality_gate.py`
- `tools/desktop_rust_post_install_probe_approval.py`
- `tools/desktop_rust_post_install_probe_result_intake.py`
- `tools/desktop_first_cargo_check_execution_boundary.py`

## 10. 2026-07-02 更新：PCWEB-090

`PCWEB-090` 已新增 `code/desktop_tauri/first-cargo-check-execution.policy.json` 和 `tools/desktop_first_cargo_check_execution_boundary.py`。该边界把 PCWEB-084 cargo-check artifact policy 与 PCWEB-089 bounded toolchain result 合并成 `explicit_manual_execution_packet_only` 报告：valid result 只能生成 `execution_packet_status=ready_for_explicit_user_approval` 的手动执行包，command/env 固定为 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 与 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`，仍保持 `safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。

它仍不运行 Cargo/Tauri/package manager、不抓依赖、不生成 `Cargo.lock` 或 target、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。下一步桌面主线应推进 `PCWEB-091` Tauri no-op shell local run smoke，除非另起显式授权增量执行真实 `cargo check`。

验证结果：

```text
PCWEB-090 RED: 9 failed, 1 warning
PCWEB-090 focused GREEN: 11 passed, 1 warning
PCWEB-090 + PCWEB-089 + PCWEB-084 + quality gate tests: 37 passed, 1 warning
docs gate: 1 passed, 2 warnings
pc-web quality gate: root 83 passed, core 34 passed, web backend 300 passed, browser smoke status ok
all-local --no-browser: ASR runtime 65 passed, ASR bake-off 18 passed, root 83 passed, core 34 passed, web backend 300 passed
```

评审后补强：

- 修复 delegated PCWEB-089 validation error 透传问题：unknown result field 名如果包含本机路径或 secret-like 字符串，PCWEB-090 只输出安全分类 `unknown result field present`。
- 扩展 path guard 覆盖：五类 forbidden roots 均在四个 path 输入上验证为读文件前阻断。

## 11. 2026-07-02 更新：PCWEB-091

`PCWEB-091` 已新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。该边界把 PCWEB-082 Tauri no-op scaffold 与 PCWEB-090 no-command first cargo-check boundary 合并成 `readiness_report_only` 报告：valid scaffold 只能生成 `smoke_packet_status=ready_for_explicit_tauri_run_approval`，并继续保持 `safe_to_run_tauri_dev_now=false`、`safe_to_run_cargo_check_now=false` 和 `safe_to_capture_audio_now=false`。

它仍不运行 Tauri/Cargo/package manager、不抓依赖、不生成 lock/target/installer、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。下一步桌面主线应推进 `PCWEB-092` desktop native bridge no-op integration，除非另起显式授权增量执行真实 Cargo/Tauri run。

验证结果：

```text
PCWEB-091 RED: 10 failed, 1 warning
PCWEB-091 initial focused GREEN: 10 passed, 1 warning
PCWEB-091 post-review focused GREEN: 12 passed, 1 warning
```

评审后补强：

- package-manager artifacts（`package.json`、`package-lock.json`、`pnpm-lock.yaml`、`yarn.lock`）会阻断 readiness，避免 package-manager 误执行后仍返回 `ready_for_explicit_tauri_run_approval`。
- no-op command function 必须映射到对应 command id，避免 `runtime_get_status` / `session_prepare` 等函数返回值交换后仍被视为 ready。
- no-op response side-effect 字段必须保持 false，`safe_to_execute_real_action`、`captures_audio`、`spawns_process`、`calls_remote_provider` 或 `writes_local_files` 漂移都会阻断 readiness。
