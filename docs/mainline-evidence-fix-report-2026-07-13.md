# 主线闭环与证据修复报告

日期：2026-07-13

## 结论

本轮已完成并验证会议 Copilot 的受控 PC 主流程：

```text
受控中文音频
-> Chrome media device
-> 本地 FunASR realtime
-> partial/final 实时文字
-> 持久化录音与会话
-> 录音期正式 AI 建议
-> 方案分析与会后纪要
-> Workbench 同一会话展示
-> 录音导出与 SHA 校验
```

本轮主线验收状态：`passed_production_mainline`。

这个状态的准确含义是：受控浏览器音频输入、真实远程 OpenAI-compatible LLM、真实本地 FunASR 和 PC Web Workbench 主流程已闭环。它不等于自然多人真实麦克风、中文技术语义质量、20 分钟 wall-clock soak 或 Mac/Windows 发布验收通过。

## 关键修复

### 1. LLM 证据不再误判

根因是后端通过项目 `.env` 加载 provider，而浏览器验收脚本的 Node 环境被清空。旧脚本因此得到 `gateway_base_url_kind=not_configured`，即使后端已经真实调用 LLM 并写入 usage ledger，也会报告 `production_llm_evidence_missing`。

现在会话事件接口返回不含密钥的 `llm_evidence`：

```json
{
  "schema_version": "llm-session-evidence.v1",
  "source": "runtime_config_and_usage_ledger",
  "configured": true,
  "provider": "openai_compatible_gateway",
  "model": "gpt-5.5",
  "is_mock": false,
  "gateway_base_url_kind": "remote",
  "llm_called": true,
  "llm_call_count": 4,
  "llm_usage_total_tokens": 2871
}
```

摘要不返回网关 URL、API key、Authorization 或请求体。验收器优先读取该摘要，只有非 mock、remote、存在正 token usage 时才允许计入 production LLM evidence。

### 2. Partial correction 不再被压成整体失败

一个会议句子可能被 ASR 切成多个 final endpoint。某些片段可以安全校正，另一些片段因为上下文不足或事实保护规则被拒绝。现在：

- 已接受 revision 保留在 canonical transcript 中。
- 被拒绝片段保留原始识别文本。
- 会话状态和 batch audit 使用 `partially_completed`。
- 页面显示“部分文字已校正，未通过安全校验的片段保留原始识别”。
- 没有可见安全 revision 时仍然 fail-closed，不为了通过门禁伪造修正。

### 3. 同一候选只显示一张建议卡

录音期自动建议和停止后正式整理可能针对同一个候选生成两张略有差异的卡。前端现在按：

```text
target_type + target_id + gap_rule_id
```

合并同一候选；正式卡到达时替换自动卡。不同候选仍独立保留，后端事件、证据和 usage ledger 不受影响。

## 新鲜主线证据

Artifact：

`code/web_mvp/backend/artifacts/tmp/browser_live_mic/current-mainline-evidence-fix2-20260713/`

关键结果：

| 项目 | 结果 |
|---|---:|
| 主线状态 | `passed_production_mainline` |
| ASR provider | 本地 `funasr_realtime`，非 mock |
| final 数量 | 4 |
| 录音期可见 partial | 9 |
| 首字延迟 | 5932ms |
| 首 final 延迟 | 12305ms |
| 录音期正式建议 | 通过 |
| 录音期首张建议 | 20148ms |
| 正式建议 | 1 张 |
| 方案分析 | 3 张 |
| 会后纪要 | 836 字符 |
| 录音导出 | HTTP 200 |
| 录音 SHA | 与 session 一致 |
| Workbench 同 session | 是 |
| 浏览器 console/network error | 0 / 0 |
| LLM provider | `openai_compatible_gateway` |
| LLM model | `gpt-5.5` |
| LLM mock | 否 |
| LLM 调用次数 | 4 |
| LLM token 总量 | 2871 |

截图：

`code/web_mvp/backend/artifacts/tmp/browser_live_mic/current-mainline-evidence-fix2-20260713/workbench-browser-live-mic.png`

说明：该截图来自主线证据采集时，保留了发现重复建议卡的现场；随后 DEC-365 已通过 focused test 和 Node VM 行为探针完成前端去重修复。该图片用于证明主线内容可见和证据可回溯，不作为去重修复后的最终视觉验收截图。

## 回归结果

- Backend：`662 passed, 2 warnings`
- Root tests：`353 passed, 2 warnings`
- ASR runtime：`89 passed, 1 warning`
- ASR bakeoff：`24 passed, 1 warning`
- Core：`34 passed, 1 warning`
- Workbench backend focused：`161 passed, 2 warnings`
- Node syntax：通过
- Python compile：通过
- `git diff --check`：通过
- 当前服务 `http://127.0.0.1:8767/health`：`ok`

## 当前仍是 No-Go 的发布门禁

这些不是本轮主流程未完成，而是生产发布前仍必须独立验收的边界：

1. 自然多人中文会议中的识别准确率、断句、说话人区分和远场串音。
2. 默认 Chrome sandbox 或桌面客户端真实系统麦克风输入；当前受控复核使用 `--no-sandbox` fake media device。
3. 20 分钟以上真实 wall-clock soak、内存增长和长会恢复。
4. L2 实时校正开启时的自然运行安全 revision 和 partial correction fresh browser evidence。
5. Mac/Windows 安装包、权限、签名、升级和真实机器验收。

下一步应从“主线实现”切换为“发布前门禁”：先重新跑一次 fresh browser screenshot 确认候选去重效果，再进行一次明确授权的真实系统麦克风验证；不要再扩展 provider 横评或重复下载音频样本。

## 关联决策

- `DEC-361`：会话快照提供不含密钥的 production LLM evidence。
- `DEC-362`：partial realtime correction 是一等状态。
- `DEC-363`：Workbench 明确展示 partial correction。
- `DEC-364`：受控浏览器主线 production LLM Go 的范围定义。
- `DEC-365`：按候选身份合并自动建议与正式建议。
