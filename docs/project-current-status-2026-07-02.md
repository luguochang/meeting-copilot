# Meeting Copilot 当前进度报告

> 日期：2026-07-02  
> 范围：`/Users/chase/Documents/面试/meeting-copilot` 当前仓库状态、已完成内容、当前红灯、后续工作。  
> 约束：本次报告未读取 `configs/local/`、未读取真实用户音频、未调用远程 ASR/LLM、未安装 Rust、未运行 Cargo/Tauri/package manager、未写入密钥。

## 1. 一句话结论

项目没有停在“无限评测”里，目前已经从早期 ASR/竞品/可行性评估，推进到：

```text
PC Local Web MVP 已形成可测骨架
Live ASR -> 状态机 -> LLM request preview -> card lifecycle dry-run 链路已形成
Tauri 桌面壳已创建静态 scaffold
桌面 Rust/Tauri 运行前安全边界已做到 PCWEB-088
PCWEB-089 result intake policy/tool 已实现并通过本地质量门禁
```

当前最准确的状态是：**PCWEB-088 已收口，PCWEB-089 已实现为 no-command result intake boundary，并已通过 focused、desktop/root、docs gate、pc-web 和 all-local --no-browser 验证。**

因此现在不是继续做无止境评测，也不是继续横向堆 dry-run 边界，而是要从 `PCWEB-089` 继续向“可运行 Tauri no-op shell”和“真实 Mac 音频链路”推进。

## 1.1 重要口径修正

旧进度报告中曾把 `PCWEB-089` 写成 `post-install read-only probe execution boundary`。这个表述已经过期，应以后续 `PCWEB-089` 计划为准。

当前正确口径是：

```text
PCWEB-089 = Desktop Rust post-install probe result intake
模式 = no-command result-intake boundary
来源 = caller-provided JSON only
行为 = 只校验人工或未来已批准探针产生的 bounded status
禁止 = 不运行 rustc/cargo/rustup/xcode-select，不运行 cargo check，不读取 PATH/shell profile/home/cache/raw output
```

也就是说，`PCWEB-089` 不是执行 Rust probe，也不是解锁 Cargo。它只是为“将来某个已批准探针结果”提供安全摄入口，防止 raw stdout、路径、命令、环境变量、缓存路径或密钥材料进入报告。

## 2. 当前项目做到哪里

### 2.1 产品和需求层

已固化的产品判断：

- 产品不是录音转文字工具，而是中文技术会议实时/准实时 Copilot。
- 核心价值在会议中持续维护结构化状态，并提醒工程缺口，例如 owner、deadline、rollback、test、metric、monitoring、risk、open question。
- 正式状态、建议卡片、会后纪要都必须 EvidenceSpan-backed，不能无证据总结。
- 非工程会议不得输出工程建议卡片。
- 默认尽量本地免费，只把 LLM 中转站作为主要远程成本；远程 ASR 不默认启用。

已落地的关键文档：

- `docs/product-requirements.md`
- `docs/implementation-roadmap.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/privacy-and-data-flow.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/project-structure.md`
- `docs/platform-packaging-and-store-compliance.md`
- `docs/project-progress-report-2026-07-02.md`

### 2.2 ASR 和评测层

已完成：

- 中文技术会议 ASR 评测集、manifest、reference、annotation、glossary。
- mock provider、command provider。
- FunASR、sherpa-onnx 的本地文件/streaming contract 验证。
- CER、技术实体召回、延迟、RTF 等指标。
- `partial/final/revision/error/end_of_stream` 风格的 streaming transcript contract。
- scheduler 已证明 `partial` 不触发 LLM，`final/revision` 才进入可调度范围。

已形成的结论：

- FunASR/Paraformer 更适合作为中文质量主候选，但依赖重、模型大、CPU 实时性接近边界。
- sherpa-onnx 更轻更快，但中文技术词质量风险更高。
- 只靠 ASR 原文不够，normalizer/stabilizer 是必选层。
- ASR bake-off 后续只作为 targeted gate，不再作为主线无限扩展。

尚未证明：

- 真实 Mac 麦克风/系统音频进入 ASR worker 的端到端延迟。
- 真实会议多人对话下的中文技术词准确率。
- 长会议稳定性、内存、CPU、模型缓存和清理策略。

### 2.3 Core 层

已完成：

- 平台无关 core，不依赖 Tauri/Electron/macOS/Windows API。
- TranscriptSegment、EvidenceSpan、SuggestionCard、StateEvent 等合同。
- meeting snapshot 聚合。
- 工程语境 gate。
- 证据链 gate。
- 降级、schema failure、timeout、invalid response 静默路径。
- 建议卡片保留、忽略、标记错误、too_late、too_intrusive 等状态基础。

意义：

- 这部分保证项目不会退化成“转写 + 会后总结”。
- 后续 Web、Tauri、Windows adapter 都应该复用 core。

### 2.4 PC Local Web MVP

已完成：

- FastAPI 本地 backend。
- 静态 Web 工作台。
- fixture session 创建、读取、删除。
- replay event stream。
- Live Mock SSE。
- Live ASR synthetic SSE。
- transcript/evidence/state/card UI 基础展示。
- provider config boundary、masked status、config validation、loader preflight 等不读密钥边界。
- OpenAI-compatible request body preview、redaction guard、schema outline preview。
- no-LLM scheduler、candidate queue、request draft queue。
- LLM schema validation dry-run、card creation policy dry-run、card lifecycle preview/append/retry/serializer/idempotency/readiness 等 dry-run 链路。

当前能证明：

- Web/API/UI 能消费受控 live envelope。
- 状态、证据、候选、request draft、card lifecycle 的合同可以被测试和展示。
- 默认质量门禁不读取 `configs/local/`，不调用远程 provider。

当前不能证明：

- 真实桌面音频采集。
- 真实 ASR worker 输入输出。
- 真实 LLM enabled executor。
- 真实建议卡片持久化和用户反馈全链路。

### 2.5 桌面端路径

已完成的桌面增量：

- `PCWEB-079`：Desktop shell readiness boundary。
- `PCWEB-080`：Desktop runtime decision boundary，推荐 `tauri_first_electron_fallback`。
- `PCWEB-081`：Desktop native bridge command contract。
- `PCWEB-082`：Tauri v2 静态 scaffold。
- `PCWEB-083`：Desktop build readiness policy。
- `PCWEB-084`：Cargo check artifact policy。
- `PCWEB-085`：Rust toolchain readiness policy。
- `PCWEB-086`：Rust toolchain installation decision policy。
- `PCWEB-087`：Rust toolchain install approval packet。
- `PCWEB-088`：Rust post-install probe approval packet。

桌面端当前事实：

- `code/desktop_tauri/src-tauri/` 已存在静态 Tauri scaffold。
- 已有 no-op Rust command handler：`runtime_get_status`、`session_prepare`、`asr_worker_health`。
- 当前没有运行 Tauri。
- 当前没有运行 `cargo check`。
- 当前没有生成 `Cargo.lock`、target、installer、bundle、签名或公证产物。
- 当前没有麦克风/系统音频采集、权限请求、ASR worker、provider config、密钥或远程调用。

## 3. 当前最新红灯

### 3.1 PCWEB-088 状态

`PCWEB-088` 已完成并加固：

- forbidden root path matching 已做大小写不敏感处理。
- `forbidden_default_side_effects` 已固定为工具侧 canonical list。
- invalid custom policy 不能控制 `pcweb_id`、`policy_name`、`policy_status`。
- 最后记录的 full local gates 已通过：
  - `pc-web`：root/core/web/browser smoke passed。
  - `all-local --no-browser`：ASR runtime、ASR bake-off、root、core、web backend passed。

这些 green 结果属于 `PCWEB-088` 收口后的状态。

### 3.2 PCWEB-089 状态

`PCWEB-089 Desktop Rust Post-Install Probe Result Intake` 已实现。

边界字段：

- `result_intake_mode=manual_result_validation_only`
- `accepted_result_source=caller_provided_json_only`
- `safe_to_accept_raw_probe_output_now=false`
- `safe_to_run_cargo_check_now=false`
- `cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`

已存在：

- `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json`
- `tools/desktop_rust_post_install_probe_result_intake.py`
- `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-089-desktop-rust-post-install-probe-result-intake.md`
- `tests/test_desktop_rust_post_install_probe_result_intake.py`

RED 验证：

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py -q
```

结果：

```text
10 failed, 1 warning
```

失败原因不是随机问题，而是预期 RED：

- policy JSON 文件不存在。
- result intake tool 文件不存在。

GREEN 和回归验证：

```text
PCWEB-089 focused: 11 passed, 1 warning
PCWEB-089 + PCWEB-088 + quality gate tests: 26 passed, 1 warning
desktop/root regression: 72 passed, 1 warning
docs gate: 1 passed, 2 warnings
pc-web quality gate: root 72, core 34, web backend 300, browser smoke ok
all-local --no-browser: ASR runtime 65, ASR bake-off 18, root 72, core 34, web backend 300
```

评审后加固：

- invalid enum error 不再回显调用方传入的 raw/path/secret-like 值。
- 任意 result validation failed 都会把 `normalized_probe_result` 重置为默认安全结果，避免失败 payload 影响报告状态。
- policy drift validation 已覆盖 `next_required_decisions` 和 `cargo_check_blockers`。
- docs gate 已纳入当前状态和进度报告，避免 PCWEB-089 完成后文档仍停在“下一步进入 PCWEB-089”的旧口径。

### 3.3 当前质量门禁口径

`PCWEB-088` 与桌面 `PCWEB-082` 到 `PCWEB-089` 边界均已通过当前本地回归。

当前应这样理解质量状态：

```text
PCWEB-088 focused/regression: green
desktop 082-088 boundary regression: green
PCWEB-089 focused: green
full root pytest / pc-web gate: green
```

因此当前 `PCWEB-089` 不再阻塞 root/pc-web gate。下一步应避免继续横向扩 dry-run，而是推进真实桌面壳或音频链路。

## 4. 当前没有做什么

为了避免误判当前进度，需要明确以下内容还没进入实现或没有默认启用：

- 没有运行真实 Tauri shell。
- 没有运行 Cargo/Tauri build。
- 没有安装 Rust。
- 没有真实麦克风/系统音频采集。
- 没有启动 ASR worker。
- 没有真实 remote ASR。
- 没有真实 LLM enabled executor。
- 没有读取 `configs/local/`。
- 没有读取真实用户音频。
- 没有写入或输出密钥。
- 没有生成安装包、签名、公证、App Store 或移动端产物。

## 5. 后续还在做什么

### 5.1 已完成：PCWEB-089

已完成：

- 新增 `rust-post-install-probe-result-intake.policy.json`。
- 新增 `desktop_rust_post_install_probe_result_intake.py`。
- 只接受 caller-provided bounded JSON status。
- 拒绝 stdout/stderr、command、path、env、cargo_home、rustup_home、api_key、authorization 等 raw/path/secret 字段。
- 即使结果显示 Rust/Cargo/Rustup 可用，也继续保持 `safe_to_run_cargo_check_now=false`。
- 继续禁止执行 `rustc/cargo/rustup/xcode-select`。
- 评审后补强 raw/path/secret-like 错误信息不回显、failed result 不污染 normalized result、policy drift 字段完整校验。

已跑验证：

```bash
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py -q
python3 -m pytest tests/test_desktop_rust_post_install_probe_result_intake.py tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

已同步更新：

- README
- `code/web_mvp/README.md`
- `code/desktop_tauri/README.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/decision-log.md`
- `docs/project-progress-report-2026-07-02.md`

docs gate 已补上，使 `PCWEB-089` 不是只存在于计划文档和 RED 测试里，而是进入 README、traceability、acceptance、privacy/data-flow、project structure、roadmap、decision log 和 Web docs gate 的正式可追溯链路。

### 5.2 PCWEB-090 后的桌面路线

推荐后续顺序：

```text
PCWEB-089
  Rust post-install probe result intake
  只验证人工提供的 bounded status，不执行 probe

PCWEB-090
  first cargo check execution boundary
  已实现为 explicit_manual_execution_packet_only
  只生成手动执行包，不运行 cargo check，不生成 Cargo.lock/target

PCWEB-091
  Tauri no-op shell local run smoke
  只验证 shell 加载 Web MVP 和 no-op IPC，不接音频

PCWEB-092
  native bridge no-op integration
  只验证 runtime/session/asr_worker health 合同

PCWEB-093+
  Mac audio capture adapter contract
  microphone/system audio permission、device、chunk、cleanup、quality events

PCWEB-094+
  ASR worker sidecar integration
  音频 chunk -> ASR final/revision -> Live ASR event -> Web 工作台
```

### 5.3 暂缓事项

以下内容暂不作为近期主线：

- 不继续扩大远程 ASR provider 横评。
- 不默认接入阿里/讯飞等付费 ASR。
- 不先做 Windows 音频采集。
- 不先做 iOS/Android 上架实现。
- 不直接启用真实 LLM 付费调用。
- 不 fork 大型会议转写项目作为主仓库，除非它能明确降低桌面音频/ASR worker 成本且不破坏 core-first 架构。

## 6. 风险判断

当前最大产品风险：

- 真实中文技术会议 ASR 准确率和实时性还没被桌面链路证明。
- 如果真实 audio -> ASR -> state -> suggestion 的端到端延迟太高，产品会退化成会后工具。
- 如果建议卡片误报或打扰过多，用户可能不会在会中使用。

当前最大工程风险：

- Tauri/Rust 还没真正跑过。
- macOS 系统音频采集涉及权限、系统版本和会议软件差异。
- 本地 ASR 模型体积、缓存、内存、CPU 和清理策略需要产品级约束。
- LLM enabled executor 需要严格控制频率、schema、成本、重试和密钥读取边界。

当前收敛建议：

- 不再开新的泛评测分支。
- `PCWEB-089` 已做绿。
- 再进入可运行桌面壳。
- 再接真实 Mac 音频。
- 再接 ASR worker。
- 最后开启真实 LLM enabled executor。

### 6.1 防止继续陷入 dry-run 循环

ASR 无限评测风险已经通过 `DEC-007` 收敛，但现在出现了另一个风险：`PCWEB-052` 到 `PCWEB-088` 期间积累了大量 provider/config/card lifecycle/desktop/toolchain 的 dry-run、disabled、preflight、policy 边界。它们对密钥、费用、文件写入和系统命令安全很有价值，但继续横向扩展会让项目看起来一直在“准备进入开发”，却没有增加真实客户端能力。

后续新增增量应满足一个硬标准：**必须让真实链路多前进一步。**

允许继续推进的例子：

- 让 `PCWEB-089` 收绿，解除当前 root gate 红灯。
- 受控进入首次 `cargo check` 边界。
- 让 Tauri no-op shell 本地运行。
- 让 no-op IPC 从客户端返回状态。
- 让 Mac 麦克风产生本地 audio chunk。
- 让 ASR worker 输出真实 `final/revision` event。
- 让 Web 工作台从真实 ASR event 生成状态候选。

暂不新增的例子：

- 再加一个 provider config dry-run endpoint。
- 再加一个 card lifecycle preview endpoint。
- 再扩一个远程 ASR 横评 provider。
- 再写一个不接近真实音频/客户端的 fixture-only UI 层。

## 7. 当前文件状态摘要

关键已存在文件：

- `code/desktop_tauri/rust-post-install-probe-approval.policy.json`
- `tools/desktop_rust_post_install_probe_approval.py`
- `tests/test_desktop_rust_post_install_probe_approval.py`
- `docs/pcweb-088-desktop-rust-post-install-probe-approval-plan.md`
- `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`
- `tests/test_desktop_rust_post_install_probe_result_intake.py`
- `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json`
- `tools/desktop_rust_post_install_probe_result_intake.py`
- `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`
- `tests/test_desktop_first_cargo_check_execution_boundary.py`
- `code/desktop_tauri/first-cargo-check-execution.policy.json`
- `tools/desktop_first_cargo_check_execution_boundary.py`

因此当前开发入口已经从 `PCWEB-090` 收口，切换为：**不要继续横向扩 dry-run，优先推进能让真实链路前进一步的 Tauri no-op shell、IPC、Mac 音频或 ASR worker 增量。**

## 8. 2026-07-02 更新：PCWEB-090

`PCWEB-090 Desktop First Cargo Check Execution Boundary` 已实现为 `explicit_manual_execution_packet_only`。它读取 PCWEB-084 artifact policy 与 PCWEB-089 bounded toolchain result，valid result 只能生成 `execution_packet_status=ready_for_explicit_user_approval` 的手动执行包，command/env 固定为 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 与 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`，并继续保持：

- `safe_to_run_cargo_check_now=false`
- `safe_to_fetch_dependencies_now=false`
- `safe_to_generate_cargo_lock_now=false`
- `safe_to_generate_target_dir_now=false`

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

- PCWEB-090 不再透传 PCWEB-089 delegated validation error 中的不可信 unknown field 名，避免路径或 bearer-like 字符串泄漏到报告。
- PCWEB-090 path guard tests 已覆盖 `configs/local`、`data/local_runtime`、`outputs`、`artifacts/tmp` 和 `data/asr_eval/samples`，并覆盖 `policy_path`、`artifact_policy_path`、`probe_result_intake_policy_path`、`probe_result_path` 四个 path 输入。

## 9. 2026-07-02 更新：PCWEB-091

`PCWEB-091 Desktop Tauri No-op Shell Local Run Smoke` 已新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。该边界为 `readiness_report_only`，只静态验证 PCWEB-082 Tauri scaffold、`devUrl=http://127.0.0.1:8765/`、`frontendDist`、minimal capability、exact no-op command catalog、generated artifact blockers 和 PCWEB-090 no-command boundary。

即使全部验证通过，也只返回 `smoke_packet_status=ready_for_explicit_tauri_run_approval`，继续保持 `safe_to_run_tauri_dev_now=false`、`safe_to_run_cargo_check_now=false` 和 `safe_to_capture_audio_now=false`。它仍不运行 Tauri/Cargo/package manager、不抓依赖、不生成 lock/target/installer、不读取 `configs/local`、不接音频/worker/密钥/远程 provider。下一步桌面主线建议推进 `PCWEB-092` desktop native bridge no-op integration，除非另起显式授权执行真实 Cargo/Tauri run。

评审后已补强：package-manager artifacts（`package.json`、`package-lock.json`、`pnpm-lock.yaml`、`yarn.lock`）会阻断 readiness；no-op command function 必须映射到对应 command id；no-op response side-effect 字段必须保持 false。
