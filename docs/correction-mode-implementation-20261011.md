# 会议校对模式与资源预检：实现与验证记录

更新：2026-10-11。本轮交付 Windows 本地网页版，不制作客户端包。安装见 [部署文档](web-deployment.md)，实测见 [资源指南](local-refinement-resource-guide-20261011.md)，步骤见 [用户手册](realtime-participation-user-manual-20261008.md)。本文发布实现、测试与失败范围，不发布私人会议标识、正文、录音或本机进程记录。

## 1. 问题与目标

本地精修因资源不足没有启动，会前界面却没清楚说明资源、实际准备状态和替代模式。模型文件齐全、预热策略开启与 worker 就绪不同；云端校对也不能证明本地精修执行。

目标是会前选择本次校对方式、查看真实资源/配置，再进入在线转写、可选本地精修和独立 L2。资源不足保留合格闭合正文、明确失败，不丢历史或伪造就绪。

## 2. 已实现合同

- preparation 保存 `correction_mode=local_and_llm|llm_only`；旧记录默认双层，遗漏字段保留已有选择。进入 WS 准备或已有输入后锁定模式，其他准备字段仍可编辑，并发会议隔离。
- `local_and_llm` 保留本地精修和独立 L2；`llm_only` 本场不持有、等待、调用或释放离线精修，仍保留在线 ASR、原文和原生 L2 队列。
- 服务启动准备在线 ASR，离线重模型延后到本地模式会议租约或明确准备。
- `/providers/asr/correction-capability` 被动报告总 RAM、提交/物理余量、预算、本地准备资格/ready/失败锁存、L2 开关/配置。`available` 是可尝试准备，`worker_ready` 是实际就绪，GET 不起模型或清失败。
- 精确资源拒绝可明确回退，但 L2 关闭/缺配置同时说明没有可用校对。探测、依赖、一般模型/传输错误不伪装成容量拒绝。保护阈值、精度和系统分页配置未改。
- 未精修正式在线段要求有声 PCM、有效闭合 ACK 和有效文本。partial、无 ACK/音频、不可信来源不晋升，实际原因和未精修来源留存。
- L2 独立处理有效正式段，保存原文/修订并验证原意、事实与数字；本地未精修不应永久阻塞合格来源。
- 会前保存选择后复查本场能力与识别状态；查看准备页和切选项不打开麦克风。
- 内存统一 GiB；`no_change` 显示“校对未改字”，混合结果显示“部分已校对（x/y）”，不修改识别原文或成功判定。

相关源码：[会议准备](../code/web_mvp/backend/meeting_copilot_web_mvp/meeting_preparation.py)、[流处理](../code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py)、[API 与生命周期](../code/web_mvp/backend/meeting_copilot_web_mvp/app.py)、[能力检查](../code/web_mvp/backend/meeting_copilot_web_mvp/correction_capability.py)、[资源保护](../code/web_mvp/backend/meeting_copilot_web_mvp/asr_resource_guard.py)。

## 3. 阶段记录

| 阶段 | 已完成 | 边界 |
| --- | --- | --- |
| R0 核实 | 分开文件、worker、资源拒绝与真实 L2 记录 | 已校对汇总不能覆盖未请求的旧正文 |
| R1 资源说明 | 区分 RAM、余量、提交、占用和暂定预算，修正粗略低配建议 | 32 GB 不是最低认证，并发峰值未测全 |
| R2 后端 | 模式保存/兼容/锁定、被动接口、跳过离线执行、准备延后 | 来源、资源与 L2 事实保护保持 |
| R3 体验 | 会前模式/回退/配置、GiB、混合状态文案 | 查看页面不自动加载或采音 |
| R4 验证 | 原生构造链路、回归、构建与实际会前界面 | 本地准备实测仍失败，不称全产品通过 |
| R5 网页交付 | Windows 安装/启停、资源与操作文档；公开稿移除私人标识和本机证据链接 | 完整旧记录只留本机，不是克隆依赖 |

## 4. 已执行测试范围

这些集合有重叠，**不得相加为总数**。构造测试证明合同，不是实时准确率或建议价值验收。

| 范围 | 结果 | 限制 |
| --- | --- | --- |
| 新后端模式 | 19 passed | 原生 WS→SQLite→L2 handler 使用 MockProvider；原文/修订并存、public semantic、模式隔离、坏源与锁存；无真实模型/设备 |
| 模式/资源/L2 相关 | 155 passed，2 skipped | 最后锁存测试补充前，包含部分新增样本 |
| 既有预热/按需 | 16 passed | 与其他集合重叠，不是模型质量验收 |
| 选定 app/import | 8 passed，217 deselected | 仅选定的启动/API 准备合同 |
| 完整 ASR stream 文件 | 100 passed | 构造流与来源回归 |
| 前端业务修改 | 12 文件，365 passed；typecheck/lint/隔离构建通过 | 对应最后 GiB 修正前版本 |
| 最后 GiB 修正 | 两文件 31 passed；最终 build 含 TypeScript 检查通过 | 旧 365 项没有在最终版本全量重跑 |
| 会后混合文案 | 36 passed | 与前端集合重叠；保留原文与明确重试 |
| 部署入口 | 三文件 44 passed；两脚本 `--help` 通过 | 无模型、付费请求、现场启停 |
| 静态检查 | 相关生产/测试 Ruff、文档引用检查通过 | 既有 React act、大 JS chunk 警告保留 |

新增 [模式回归文件](../code/web_mvp/backend/tests/test_meeting_correction_mode.py) 可用已安装的后端环境重复，从仓库根目录执行：

```powershell
Push-Location code/web_mvp/backend
.venv/Scripts/python.exe -B -X utf8 -m pytest tests/test_meeting_correction_mode.py -q --tb=short
Pop-Location
```

部署合同测试：

```powershell
Push-Location code/web_mvp/backend
.venv/Scripts/python.exe -B -X utf8 -m pytest tests/test_web_quality.py tests/test_workbench_server_tool.py tests/test_workbench_server_runtime_identity.py -q --tb=short
Pop-Location
```

## 5. 实际页面与保留的失败

更新的源码与前端已受管加载。会前实际检查两模式、真实 GiB、容量回退、L2 开关/配置和锁存提示，在线识别 ready。打开准备页、切模式、GET 查询没有启动离线模型或麦克风，也没有新增远端请求。

随后资源暂恢复时明确发起一次本地准备：提交约 8.62 GiB、物理约 7.11 GiB，约 35.622 秒后提交余量降至 2,063,749,120 B（1.922 GiB），触发 2 GiB 保留量。结果 `asr_resource_guard_triggered`、离线未 ready、进程退出、清理完成、`recovery_required=true`、推理请求 0，在线继续 ready。

失败保留，未自动重试或降低保护。后续仅大模型选择和被动查询正常，没有再次启动离线模型。原始音频、正文、校对结果与配置保持；本阶段没有新增麦克风、播放音频、远端请求或客户端打包。

## 6. 未完成验收

- 全新 Windows 安装、真实模型下载和更低规格没有在本轮完整复验。
- 32 GB 参考机器此次本地准备失败，完整精修与失败后恢复质量未通过。
- 双模型峰值、长会议/双轨、真实收音准确率、端到端分布、连续教练价值与用户最终验收仍开放。
- 仅大模型层仍依赖本地在线 ASR，不能重听遗漏声音，也不保证修正全部错词；原意/事实校验失败保留原文。
- 未请求校对的历史正文不会因切模式自动已校对，匿名成功统计不覆盖另一份缺口。
- 网页系统音频、可靠双轨及多人归属不是本轮已交付能力；不打客户端包。
