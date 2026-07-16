# Meeting Copilot 依赖、模型与来源门禁

> 状态：Phase 0 供应链基线
> 日期：2026-07-14
> 适用范围：backend Python、FunASR 代码与模型、FFmpeg、Tauri/Rust、前端资产，以及外部项目代码移植

## 1. 目的与边界

本文区分三件容易被混为一谈的事情：

1. 代码可以在开发机运行；
2. 依赖版本可以从干净环境复现；
3. 代码、模型和二进制有权被重新分发到商业安装包。

只有第三项通过，组件才允许进入 Mac/Windows 安装包。开源项目代码的许可证不能自动覆盖模型权重、训练数据、预编译二进制、字体、图标或其他资产。

当前仓库没有根级 `LICENSE`、`NOTICE`、SBOM 和正式第三方许可证包，因此当前状态仍为 **开发可用、公开分发 No-Go**。

## 2. Backend Python 锁定策略

权威文件：

- `code/web_mvp/backend/pyproject.toml`：直接运行时依赖、Python 版本边界和 `dev` 依赖组。
- `code/web_mvp/backend/uv.lock`：完整解析结果、平台 wheel/sdist URL 与哈希，是 CI 的唯一 Python 安装来源。
- `.github/workflows/ci.yml`：固定使用 `uv 0.11.28`，所有 Python job 执行 `uv sync --frozen --group dev`。

运行时依赖包含 API/WebSocket、LLM HTTP 客户端、multipart 上传、结构化日志、麦克风诊断、WebSocket 麦克风客户端和录音导入所需的 `imageio-ffmpeg`。测试和 lint 仅存在于 `dependency-groups.dev`，不得在 CI 中另写 `pip install` 清单。

标准命令：

```bash
cd code/web_mvp/backend
uv lock --check
uv sync --frozen --group dev
PYTHONPATH=.:../../core uv run --frozen python -c "import fastapi, httpx, imageio_ffmpeg, pydantic, sounddevice, structlog, uvicorn, websocket, websockets; import meeting_copilot_core, meeting_copilot_web_mvp.app"
```

规则：

- Python 支持范围固定为 `>=3.11,<3.14`，CI 基准为 CPython 3.12；升级边界必须单独验证后修改 lock。
- PR 修改 `pyproject.toml` 时必须同时更新 `uv.lock`；`uv lock --check` 失败即阻断。
- CI 和发布构建只能使用 `--frozen`；不得临时 `pip install` 修复缺包。
- 常规依赖升级由独立 PR 完成，记录旧/新 lock diff、许可证变化和最小 smoke 结果。
- `uv.lock` 证明版本和制品哈希可复现，不等同于许可证审查或漏洞扫描通过。

`meeting_copilot_core` 是仓库内 first-party 模块，目前没有独立 `pyproject.toml`，因此开发、测试和 E2E 仍通过 `PYTHONPATH=.:../../core` 加载。它不是漏锁的 PyPI 依赖，但也是 standalone backend bundle 的明确阻塞：打包阶段必须将 core 变成可安装的本地 package，或作为受控源码目录随 backend runtime 一并打包并记录 source tree hash；不得依赖用户仓库路径。

## 3. FunASR 代码与 Python runtime

| 项目 | 当前来源 | 当前锁定 | 许可证判断 | 安装包门禁 |
|---|---|---|---|---|
| FunASR 代码 | [modelscope/FunASR](https://github.com/modelscope/FunASR) | `code/asr_runtime/requirements-funasr.lock` 中 `funasr==1.3.10` | 上游仓库声明 MIT；必须随包保留对应版本许可证文本和 copyright | 当前 requirements 只有版本，没有制品哈希；生成带 hash 的 lock/wheelhouse、SBOM 并离线复现前 No-Go |
| ModelScope SDK | PyPI `modelscope` | `modelscope==1.37.1` | 以实际安装制品 metadata 和上游许可证为准 | 与 FunASR runtime 一起生成 SBOM、NOTICE 和漏洞报告 |
| PyTorch/torchaudio 等 | PyPI wheel | 由 `requirements-funasr.lock` 固定版本 | 每个 wheel 独立审查；还要记录平台、CPU/GPU backend | arm64/x86_64 分别离线安装并验证哈希、动态库和许可证 |

FunASR 是独立、较重的 Python 3.11 sidecar runtime，不进入 backend `uv.lock`。两套环境不得混装；桌面打包时必须分别产出 backend runtime manifest 和 ASR runtime manifest。

## 4. FunASR 模型权重

当前代码实际涉及以下 ModelScope IIC 模型：

| 用途 | Model ID | 本机缓存 README 声明 | 当前可分发结论 |
|---|---|---|---|
| 录音导入/会后批量 ASR | `iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | 以本机缓存和 ModelScope 页面审查为准 | No-Go，尚未固定不可变 revision 和完整制品 SHA-256 manifest |
| 实时中文 ASR | `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` | Apache License 2.0 | No-Go，尚未固定不可变 revision 和完整制品 SHA-256 manifest |
| VAD | `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | Apache License 2.0 | No-Go，尚未固定不可变 revision 和完整制品 SHA-256 manifest |
| 中英文标点 | `iic/punc_ct-transformer_cn-en-common-vocab471067-large` | Apache License 2.0 | No-Go，尚未固定不可变 revision 和完整制品 SHA-256 manifest |

本机缓存 `.mv` 记录的是可变的 `Revision:master`，不能作为发布版本。进入 bundle 前必须完成：

1. 在 ModelScope 上解析并记录不可变 revision/commit，而不是 `master` 或模型 alias。
2. 保存模型页面、README 和完整 Apache-2.0 许可证文本的审查副本。
3. 对 bundle 内每个文件生成路径、字节数和 SHA-256 manifest，并绑定到 app release commit。
4. 确认模型页允许权重再分发和商业使用；若条款只覆盖代码或存在额外使用限制，必须阻断。
5. 在 `NOTICE` 中列出模型名称、来源 URL、revision、许可证和修改/量化情况。
6. 验证安装包不会在首次会议时静默联网下载或漂移到新权重。

模型下载审批文件 `code/asr_runtime/funasr-model-download-approval.policy.json` 只控制下载动作，不是再分发授权证明。

## 5. FFmpeg

Backend 通过 `imageio-ffmpeg` 优先解析 FFmpeg，开发环境还会回退到系统 `ffmpeg`。供应链必须分别审查：

- Python wrapper `imageio-ffmpeg` 的版本、制品哈希和许可证；这些由 backend `uv.lock` 固定。
- wrapper wheel 内或桌面包内的 FFmpeg 可执行文件；其许可证取决于实际构建配置，不由 wrapper 许可证代替。
- 当前开发机 Homebrew FFmpeg 8.1.1 显示 `--enable-gpl`、`libx264` 和 `libx265`，不得把该本机二进制直接复制进安装包。

发布门禁：

1. 选择并固定官方可追溯或自建的 FFmpeg 二进制来源、版本、平台和 SHA-256。
2. 保存 `ffmpeg -version`、`ffmpeg -buildconf` 和 `ffmpeg -L` 输出。
3. 法务/许可证审查明确 LGPL/GPL 配置、动态或静态链接方式、源代码/offer 和 NOTICE 义务。
4. arm64、x86_64、Windows 分别记录制品；禁止依赖用户 Homebrew、PATH 或任意系统版本作为产品主链路。
5. 许可证材料、构建脚本和对应源代码获取方式进入发布归档后才允许 bundle。

默认推荐生产受控的 LGPL-compatible 构建；若确需 GPL codec，必须先书面接受相应分发义务。

## 6. Tauri 与 Rust

- 权威依赖解析为 `code/desktop_tauri/src-tauri/Cargo.lock`；`Cargo.toml` 中的宽版本约束不能替代 lock。
- Tauri 本身通常按 Apache-2.0/MIT 双许可证发布，但每个传递 crate 必须依据 lock 中的准确版本单独确认。
- CI 必须使用 `cargo check --locked`；正式构建必须使用 `cargo build --locked` 或 Tauri 等价 locked 模式。
- 进入 Mac Alpha 前生成 Cargo SBOM/许可证清单，阻断未知许可证、不可接受 copyleft、撤回 crate 或 checksum 不一致。
- Tauri/WebView 只解决应用壳层许可证，不覆盖嵌入的 Python、ASR 模型、FFmpeg 和前端资产。

## 7. 前端代码与资产

当前权威 Workbench V2 位于 `code/web_mvp/frontend_v2`，使用 React/TypeScript/Vite，并已有 `package.json` 与 `package-lock.json`；运行时依赖包含 React、React DOM、React Markdown 和 Lucide React。旧 `frontend_static` 仍是仓库内第一方静态兼容页面。`ui-ux-pro-max` 是开发设计辅助，不是产品运行时依赖，不应进入安装包或 SBOM。

门禁：

- V2 必须提交 `package.json` 和唯一的 `package-lock.json`，CI/发布构建使用 `npm ci`；当前 lockfile 尚未进入干净 release commit，因此不能作为已发布供应链证据。
- 前端 production 与 dev dependency 都要进入 SBOM；NOTICE 至少覆盖运行时依赖，许可证扫描还必须覆盖构建工具链。禁止 CDN 运行时加载。
- 字体、图标、图片、音频样本和复制的 CSS/JS 都必须记录来源、版本/commit、原始哈希、许可证和修改说明。
- 当前仓库缺少根级产品许可证；在明确第一方代码许可证前不得公开分发源码或二进制。

## 8. Meetily 选择性移植

[Meetily](https://github.com/Zackriya-Solutions/meetily) 可作为桌面采音、checkpoint、sidecar、迁移和更新器的工程参考，但不能用“项目是 MIT”代替逐文件来源记录。

每次移植必须先提交 provenance 记录，至少包含：

- 上游仓库 URL、不可变 commit、原始文件路径和原始文件 SHA-256；
- 该 commit 下的 LICENSE/copyright；
- 移植到本仓库的目标文件、修改摘要和责任人；
- 是否还依赖第三方代码、模型、二进制或资产；
- NOTICE/源码头要求和行为测试证据。

未完成记录前只能研究架构，不能复制代码。当前源代码扫描未发现 Meetily 运行时代码进入产品；未来审查应以 Git diff 和 provenance ledger 为准。

## 9. Screenpipe 禁止使用

[screenpipe](https://github.com/mediar-ai/screenpipe) 当前采用 Screenpipe Commercial License。它与本产品在长期音频采集、转写和会议助手方向存在竞争/嵌入风险。

硬门禁：

- 禁止复制、改写、翻译或链接当前 screenpipe 源码；禁止把其 crate、npm package、二进制、模型包或资产加入本产品。
- 禁止通过历史 MIT commit 规避当前许可证，除非法律审查完成精确 commit provenance 和所有后续代码来源隔离。
- 可以阅读公开文档并独立实现通用思想，但设计记录不得包含可还原的源码片段。
- 只有取得明确商业授权并完成书面许可证审查后，才能通过新的 ADR 解除本门禁。

## 10. 发布供应链 Checklist

以下任一项缺失，安装包状态均为 No-Go：

- [ ] backend `uv lock --check` 与 `uv sync --frozen --group dev` 在 clean checkout 成功。
- [ ] FunASR sidecar 使用带制品哈希的独立 lock/wheelhouse，可离线复现。
- [ ] Python 与 Cargo SBOM、漏洞扫描、第三方 LICENSE/NOTICE 完整。
- [ ] 四个实际运行模型均绑定不可变 revision、文件 SHA-256 和可再分发许可证证据。
- [ ] FFmpeg 二进制来源、构建配置、许可证义务、源代码获取方式和哈希完整。
- [ ] Meetily 的每个移植文件存在逐文件 provenance；不存在 screenpipe 代码或制品。
- [ ] 第一方根级 `LICENSE`、隐私说明、版本号、source commit 和最终 app hash 已绑定。
- [ ] CI evidence 由 clean checkout 生成，且没有临时安装、未锁依赖或复用旧制品。

## 11. Phase 0 当前结论

Backend 运行时、测试和 lint 已统一进入 `uv.lock`，CI 不再维护重复的 pip 依赖清单。这解决了 backend Python 的版本漂移问题，但没有自动解决 ASR 独立环境、前端 npm 供应链、模型权重、FFmpeg 二进制和桌面安装包的再分发问题。

2026-07-15 已新增机器可读 `configs/release-provenance.json` 和 fail-closed `tools/release_provenance_manifest.py`。当前清单 `artifacts/tmp/release_provenance/phase0-current-worktree-20260715/manifest.json` 验证了开发 DMG 与其 evidence 的路径和 SHA-256 一致，同时明确阻断 dirty/untracked source、tracked sensitive path、缺失的根 LICENSE/NOTICE/SBOM、四个实际运行模型以及 FFmpeg 的 unresolved revision/hash/redistribution。门禁实现完成不等于门禁通过；这些组件在材料闭环前继续保持 No-Go。
