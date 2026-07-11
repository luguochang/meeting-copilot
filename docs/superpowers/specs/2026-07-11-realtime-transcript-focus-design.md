# Realtime Transcript Focus Design

> 日期：2026-07-11  
> 状态：视觉方案 A 已由用户确认；等待书面规范复核后进入实施计划  
> 关联产品：Meeting Copilot PC Workbench

## 1. 目标

把 Workbench 从“持续堆叠 ASR 中间结果的调试页面”收敛成会议中可长期使用的双栏工作台：

- 左栏持续显示可读、稳定、不会重复膨胀的会议文字。
- 右栏固定显示实时提醒和正式 AI 建议，不被长文字流挤出首屏。
- 用户能明确区分本地实时识别、已确认文字、AI 修正和正式 AI 建议。
- 保持录音、证据回点、历史记录和会后复盘的可追溯性。
- 除已有 OpenAI-compatible LLM 中转站外，不新增远程 ASR 或其他收费服务。

## 2. 已确认根因

当前真实会话 `rec_mrg5l9ri` 持续约 18 分钟，后端只有 1 条 final，但实时 partial 累计约 4543 字。前端针对同一 segment 每增长至少 12 个字就调用 `appendPartialDraftUtterance()` 新增一条 `已记录`，因此页面不断向下追加。

当前三类内容同时存在：

- `transcript_partial` 被拆成多条 `partial-draft`。
- `transcript_final` 到达后再追加一条正式发言。
- `transcript_revision` 在结束会议后再追加一条 AI/normalizer 修正文稿。

虽然 final 会尝试删除同 segment 的 partial draft，但 FunASR 长时间只维持一个增长 segment、final 极少，因此录音期间草稿会持续铺满页面。

正式 AI 建议并非没有调用。当前真实会话已执行 3 次自动建议并生成 3 张 suggestion card，但它们位于长文字流之外，且“实时提醒”“实时建议”“AI 建议”的命名接近，用户难以判断哪部分来自 LLM。

当前 L2 LLM ASR 修正只在 WebSocket 收到 `END` 后对累计 finals 执行。录音期间只执行本地 normalizer，不会产生可见的 LLM revision；final 极少时，用户自然看不到“AI 正在修正文字”。

## 3. 方案选择

### 3.1 布局

采用用户确认的方案 A：双栏聚焦。

- 左侧主栏占可用宽度约 65%，用于会议文字。
- 右侧固定栏占约 35%，用于实时提醒和正式 AI 建议。
- 顶部只保留会议标题、录音状态、时长、结束会议和必要的溢出菜单。
- 原左侧驾驶舱数字列不再作为独立视觉列；关键状态合并到顶部和右栏分区标题。
- 会后复盘、方案分析和导出在结束会议后进入同一工作区的复盘状态，不在录音中占据主要空间。

### 3.2 转写更新策略

采用“单一活动草稿 + 已提交段落”模型：

1. 每个活动 segment 最多对应一个 `live-partial` DOM 节点。
2. 同 segment 的后续 partial 只更新该节点文本，不新增历史行。
3. final 到达时，将对应活动草稿原位转为 committed transcript row。
4. final 已存在时，不再保留同 segment 的 partial draft。
5. 新 segment 开始后，才创建下一条活动草稿。
6. 页面默认只显示 committed finals 和当前活动 partial，不显示历史 partial 增量。

原始 partial 事件仍可保存在后端诊断证据中，但不作为普通用户可见时间线项目。

### 3.3 AI 修正策略

不采用以下两种极端方案：

- 继续仅在 END 后整段修正：费用低，但会议中无法看到修正结果。
- 每个 partial 都调用 LLM：费用、延迟和文本抖动不可接受。

采用有界混合策略：

1. partial 和 raw final 先经过本地 normalizer，立即用于 UI。
2. 接受通过的稳定 final 才有资格进入 LLM 修正，不对 partial 调用 LLM。
3. 当 final 同时触发正式建议候选时，优先在同一次 OpenAI-compatible 请求中返回：
   - `corrected_transcript`
   - `suggestion_card`
   - 对应 evidence span
4. 没有建议候选的 final 进入修正批次；批次满足任一条件才调用：
   - 距离上次修正达到 30 秒；
   - 累计新增 final 文本达到 240 个中文字符；
   - 用户结束会议。
5. 单场会议同时只允许一个修正请求在途；在途期间新增 final 合并到下一批。
6. 修正失败时保留本地 normalizer 结果，不阻塞实时文字、录音或正式建议。

该策略复用已有中转站，不新增 provider，并显著少于“每个 final 单独修正”的调用次数。

### 3.4 Revision 展示

- LLM 修正返回后产生 `transcript_revision`，必须引用原 final 的 segment/evidence。
- 前端按 segment key 原位替换 committed row，不在时间线末尾追加重复段落。
- 修正后的行显示低干扰状态 `AI 已校正`。
- 用户可展开“查看原始识别”，原文只在需要复核时出现。
- evidence clickback 默认落到当前修正文稿；审计视图仍能查看 raw final 和 revision 关系。
- LLM 不得新增原始转写中不存在的事实、实体、负责人、时间或结论。

## 4. 右栏信息架构

右栏固定为两个分区：

### 实时提醒

- 来源：本地 deterministic `partial_hint_event` 和尚未执行的 suggestion candidate。
- 语义：提示用户“这里可能需要确认”，不声称已经完成 LLM 分析。
- 默认最多显示 3 条，按决策、待办、风险、待确认问题排序。
- 重复提醒按 dedupe key 原位更新，不不断追加。

### AI 建议

- 来源：真实 LLM suggestion card。
- 每张卡必须显示 `AI 建议` 标签、建议正文、触发原因和可点击证据。
- 新建议出现时固定在右栏顶部，并通过轻量高亮提示；不使用弹窗打断会议。
- 没有正式建议时显示明确空态：`正在根据已确认发言分析`。
- LLM 不可用时显示失败状态，不把本地提醒伪装成 AI 建议。

## 5. 页面状态

### 录音中

- 左栏：已提交文字 + 一条正在听。
- 右栏：实时提醒 + AI 建议。
- 顶部：录音状态、时长、麦克风输入状态、结束会议。
- 不展示方案分析、完整纪要和导出操作。

### 整理中

- 停止继续采集 partial。
- 保留当前文字，不清空或整页替换。
- 显示 `正在校正文字并生成复盘`，逐项更新完成状态。

### 已复盘

- 左栏默认显示校正后的完整文字，可切换查看原始识别。
- 右栏切换为建议、方案、纪要三个紧凑标签页。
- 导出文字、纪要、录音和删除会议进入顶部操作菜单。

## 6. 数据和接口边界

前端需要一个按 segment 聚合的 transcript view model：

```text
segment_id
raw_partial_text
raw_final_text
normalized_final_text
corrected_text
display_text
status = partial | final | corrected
evidence_ids[]
updated_at_ms
```

WebSocket 事件保持兼容，但渲染语义调整：

- `transcript_partial` -> upsert 活动 segment。
- `transcript_final/final` -> commit 同 segment。
- `transcript_revision` -> replace 同 segment 的 display text。
- `partial_hint_event` -> upsert 右栏实时提醒。
- `suggestion_candidate_event` -> 候选状态，不直接显示为正式 AI 建议。
- suggestion card API response -> upsert 右栏正式 AI 建议。

LLM 修正与正式建议的组合响应必须使用结构化 schema；解析失败不得用字符串猜测恢复。

## 7. 错误与降级

- ASR partial 连续增长但没有 final：页面仍只显示一条活动草稿，并提示 `等待断句确认`。
- final 超过 30 秒没有产生：记录 latency warning，但不复制 partial。
- LLM 修正超时：保留 normalized final，标记 `AI 校正暂不可用`，稍后批次可重试一次。
- 正式建议失败：实时提醒继续工作，AI 建议区显示不可用状态。
- revision 无法找到目标 segment：进入审计日志并显示为独立“修正补充”，不得静默丢失。
- 页面刷新或打开历史：从 events 重建 segment view model，最终结果必须与实时增量渲染一致。

## 8. 性能和成本门禁

- 同一活动 segment 的 transcript DOM 节点数量始终为 1。
- 30 分钟连续会议中，DOM transcript row 数应接近 final/revision segment 数，而不是 partial event 数。
- partial 更新不得触发 LLM。
- LLM 修正请求受 single-flight、30 秒窗口和 240 字批次控制。
- 建议与修正组合请求优先于独立修正请求。
- 每次请求记录 provider、model、用途、token usage、关联 segment 和是否降级，不记录 secret。

## 9. 验收标准

### 自动化

- 同 segment 连续 100 个 partial，页面始终只有 1 条活动草稿。
- final 到达后活动草稿消失并原位变为 1 条 committed row。
- revision 到达后 committed row 文本被替换，DOM row 数不增加。
- 刷新 session 后的文字行数和内容与实时状态一致。
- 正式 AI 建议卡始终位于右栏，包含 evidence link。
- 本地提醒不能计入正式 AI 建议数量。
- partial 不产生 LLM correction 调用。
- final suggestion + correction 能合并为一次 LLM 调用。
- 无建议 final 按批次阈值触发修正，不逐条调用。

### 浏览器真实链路

- 使用真实麦克风或本机外放进入真实麦克风，连续录音至少 10 分钟。
- 录音期间文字持续更新，但不会出现 partial 增量铺满页面。
- 至少一条 final 在录音中被 LLM 校正并显示 `AI 已校正`，原始识别可展开。
- 至少一张同 session、带 evidence 的正式 AI 建议卡在录音期间固定可见。
- 结束会议后录音保存、完整文字、建议、方案、纪要和导出仍闭环。
- 浏览器 console/network error 为 0。

## 10. 非目标

- 本轮不更换本地 ASR provider。
- 本轮不新增远程收费 ASR。
- 本轮不实现说话人分离或声纹识别。
- 本轮不重做历史记录、移动端或安装包外壳。
- 本轮不允许 LLM 基于不稳定 partial 生成永久正式卡。
