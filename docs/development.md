# 开发指南

## 目录职责

- `code/core/`：领域模型和平台无关逻辑。
- `code/web_mvp/backend/`：FastAPI API、SQLite、音频和 provider。
- `code/web_mvp/frontend_v2/`：React 工作台。
- `code/desktop_tauri/`：Tauri 壳、后端监督器和原生音频。
- `code/asr_runtime/`：本地 ASR 和文件处理。
- `tests/`：跨模块、打包和发布工具测试。
- `website/`：独立静态官网。

## 本地开发

安装与双终端启动方式见 [安装指南](installation.md)。提交前不要把本地数据、模型、录音、密钥或构建产物移出已忽略目录。

## 质量检查

前端：

```powershell
cd code\web_mvp\frontend_v2
npm run lint
npm run typecheck
npm test
npm run build
```

后端与核心：

```powershell
cd code\web_mvp\backend
uv sync --frozen --group dev
uv run --frozen ruff check meeting_copilot_web_mvp
uv run --frozen pytest -q
uv run --frozen pytest -q ../../core
```

跨模块工具：

```powershell
cd <repository-root>
code\web_mvp\backend\.venv\Scripts\python -m pytest -q tests
```

桌面端与官网：

```powershell
cargo check --locked --manifest-path code\desktop_tauri\src-tauri\Cargo.toml
cd website
npm ci
npm run check
```

## 变更原则

- API 合同变化必须同步更新前端类型和后端测试。
- 会议、音频、provider 和删除逻辑属于高风险路径，需要对应回归测试。
- UI 需检查 `1440x900`、`1024x768` 和 `390x844`，同时覆盖弹窗、空状态、处理中、失败和完成状态。
- 不在前端记录密钥，不在错误响应中返回本机绝对路径。
- 不提交 `artifacts/`、`data/local_runtime/`、模型目录或任何真实会议数据。

## 数据库与兼容性

应用启动时执行 SQLite schema 迁移。修改持久化结构时应保持迁移可重复，并使用临时数据目录验证新建数据库和已有数据库升级两条路径。

## 依赖与供应链

- Python、Node 和 Rust 依赖分别由 `uv.lock`、`package-lock.json` 和 `Cargo.lock` 固定。
- `sbom.cdx.json` 提供机器可读依赖清单。
- 模型和二进制文件在 `configs/release-provenance.json` 中记录来源与再分发状态。
- 未明确许可、版本和哈希的模型或二进制文件不能进入公开安装包。
