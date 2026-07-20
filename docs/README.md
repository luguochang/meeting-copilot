# 文档目录

Meeting Copilot 的文档按用途分层。历史文档保留在 `docs/` 根目录，是为了避免破坏已有证据、决策日志和测试中的路径引用；新增交接材料放入 `docs/handoff/`。

## 目录分类

| 目录 | 内容 | 是否可作为发布结论 |
| --- | --- | --- |
| `docs/handoff/` | 环境交接、Windows 开发和下一位 AI 的接手指令 | 仅作为执行入口，不能替代验收 |
| `docs/` 根目录 | SDD/TDD 计划、需求、架构、决策、评测和发布报告 | 以文档中明确的 evidence scope 为准 |
| `data/asr_eval/` | 中文 ASR 评测集、参考文本、标注、术语表、manifest 和受控样本 | 受控评测素材，不等于自然多人会议质量 |
| `data/web_mvp/fixtures/` | Web 状态、会议和降级场景 fixture | 仅用于确定性测试，不是用户会议数据 |
| `data/product_value_gate/` | 产品价值触发点和语义场景数据 | 只用于产品逻辑测试 |
| `code/asr_runtime/model_packs/` | 本地模型包的版本、hash、许可证和路径 manifest | 不包含大模型本体 |
| `code/web_mvp/frontend_v2/src/assets/` | Web/桌面共享 UI 的品牌位图资源 | 随前端构建进入产品资源 |
| `artifacts/` | 本机临时报告、截图、录音、数据库、构建和运行产物 | 已被 `.gitignore` 排除，不提交 |

## 交接入口

新环境首先阅读：

1. [Windows 开发交接手册](handoff/windows-development-2026-07-20.md)
2. [全量路线执行清单](full-roadmap-execution-checklist-2026-07-18.md)
3. [阶段缺口与路线讨论](post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md)
4. [运行约束](runtime-operating-constraints.md)
5. [决策日志](decision-log.md)

## 敏感数据规则

- 真实 API Key 只通过本地 Provider 设置或临时环境变量注入，不能写入 Git。
- `data/asr_eval/` 中的公开/受控样本必须保留来源和许可证信息；用户录音只能放在本机 `artifacts/` 或 runtime data directory。
- evidence 文档可以提交摘要、hash、状态和脱敏指标，不能提交原始音频、完整会议文字稿、Authorization header 或本机私有路径。
