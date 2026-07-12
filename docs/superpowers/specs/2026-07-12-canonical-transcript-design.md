# Canonical Realtime Transcript Design

> 日期：2026-07-12
> 状态：用户已确认按推荐方案实施
> 关联产品：Meeting Copilot PC Workbench
> 替代范围：细化并替代 `2026-07-11-realtime-transcript-focus-design.md` 中仅按 segment row 展示实时文字的部分；双栏布局、AI 建议和限频校正边界继续有效

## 1. 问题定义

Workbench 当前把 ASR 的内部事件流直接投影成用户可见文字：

- FunASR worker 返回从会议开始累计到当前的 hypothesis。
- 服务端 VAD endpoint 将累计 hypothesis 切成多个 final。
- 前端同时处理 `text`、`normalized_text`、partial、final 和 revision。
- 页面刷新或 WebSocket 断开后，当前会议不会稳定恢复。

这使局部去重与用户核心目标发生冲突。用户不关心 ASR event 数量或 segment 生命周期；用户需要的是一份从会议开始持续增长、始终完整、连续可读、可以追溯修正的会议文字稿。

现场会话 `rec_mrh7w0eb` 证明数据层已有 24 条 final、988 个已确认字符，但活动 partial 仍携带 992 字累计 `normalized_text`。因此完整内容虽然存在，页面仍会出现巨大累计草稿、碎片段落或刷新后空白。

## 2. 产品原则

### 2.1 用户看到的是文档，不是事件日志

页面主文字区只展示 canonical transcript document：

```text
canonical transcript = committed paragraphs + optional active tail
```

ASR raw events、累计 source snapshot、provider metadata 和 revision audit 继续保存，但不直接成为普通用户可见行。

### 2.2 完整内容始终可见

从出现第一段有效语音开始，到录音、整理、复盘和历史恢复，完整会议文字不得被清空。停止会议、生成建议、生成纪要、页面刷新和后端重连都不能让已确认正文消失。

### 2.3 只有一个活动尾部

录音期间最多存在一个 `active_tail`：

- 用户讲话时持续原位更新。
- 用户短暂停顿不会复制全文。
- endpoint final 到达时，活动尾部原位转入已确认正文。
- 下一段语音开始后创建新的活动尾部。

### 2.4 静音用于确认，不用于决定何时显示

partial 一到达就显示新增内容。900ms 静音只用于确认当前尾部，不允许用户必须停顿后才看到刚才说的话。

### 2.5 AI 校正原位修改

revision 替换目标 segment 的显示文字，不新增重复段落。修正后的正文显示低干扰 `AI 已校正`；原始识别只在用户展开时出现。

## 3. 方案选择

采用后端 canonical projector + 前端 canonical document renderer。

不采用仅修补现有 DOM 的方案，因为它继续让前端解释 provider-specific 累计语义；不采用单一全文 textarea，因为它无法稳定支持 EvidenceSpan、段落 revision、时间定位和后续说话人识别。

## 4. 数据模型

### 4.1 原始事件层

原始层保留：

```text
event_type
provider_segment_id
source_snapshot_text
raw_text
normalized_text
start_ms
end_ms
confidence
provider metadata
```

FunASR 累计 source snapshot 必须保留用于审计和完整性校验，但不能作为活动尾部直接展示。

### 4.2 Canonical segment

```text
segment_id: string
sequence: integer
start_ms: integer
end_ms: integer
raw_text: string
normalized_text: string
corrected_text: string | null
display_text: string
status: partial | final | corrected
evidence_ids: string[]
updated_at_ms: integer
```

规则：

- `display_text = corrected_text || normalized_text || raw_text`。
- 同 segment 权威等级为 `partial < final < corrected`。
- 低权威事件不能覆盖高权威事件。
- revision 必须引用目标 final；无法解析的 revision 进入审计补充，不静默丢失。

### 4.3 Canonical transcript snapshot

`GET /live/asr/sessions/{session_id}/events` 在保持原字段兼容的同时增加：

```json
{
  "canonical_transcript": {
    "schema_version": "canonical-transcript.v1",
    "session_id": "rec_xxx",
    "segments": [],
    "active_tail": null,
    "committed_text": "...",
    "full_text": "...",
    "committed_char_count": 0,
    "full_char_count": 0,
    "updated_at_ms": 0
  }
}
```

完整性不变量：

```text
full_text == concat(segments.display_text) + active_tail.display_text
```

## 5. FunASR 累计文本边界

FunASR 的 `source_snapshot_text` 是累计快照。服务端维护 `committed_source_snapshot_text`：

- 当前快照以前缀包含已提交快照时，只取新增后缀作为当前尾部。
- `text` 与 `normalized_text` 必须同时基于新增后缀生成，不能一个增量、一个累计。
- 当前快照与前一快照发生小范围重识别时，不通过简单字符串截断猜测；保留原始快照，并使用受限公共前缀/重叠算法生成候选增量。
- 无法安全映射时不丢弃内容：canonical projector 使用最新完整 snapshot 做完整性补偿，并标记 `projection_reconciled=true`。
- final/revision/export 均以 canonical snapshot 为准，raw source snapshot 只用于审计。

## 6. 段落模型

用户看到的是自然段，不是每次 900ms 静音一行。

相邻 final segment 在满足以下条件时合并为一个视觉段落：

- 时间间隔不超过 3 秒；
- 合并后不超过 180 个中文字符；
- 前一段没有明显结束标点，或当前段过短；
- 不跨越 revision/evidence 的可定位边界。

段落 DOM 可以包含多个带 `data-segment-id` 的 span，从而同时实现连续阅读和证据回跳。每个段落只显示起始时间，不重复显示“发言”标签。

## 7. 前端交互

### 7.1 录音中

- 左栏显示全部 committed paragraphs。
- 正文末尾显示一条 `正在识别` active tail。
- partial 更新只修改 active tail，不重建全部正文。
- final/revision 到达时重建受影响段落或 committed document。
- 顶部只显示一个结束会议按钮。

### 7.2 自动滚动

- 用户位于正文底部 96px 范围内时，更新后自动跟随。
- 用户向上滚动阅读旧内容时，不强行改变滚动位置。
- 有新内容时显示固定的 `↓ 有新内容` 按钮。
- 点击后滚动到底部并恢复自动跟随。
- `prefers-reduced-motion` 下使用即时滚动，不做平滑动画。

### 7.3 整理和复盘

- 停止录音后保留完整正文。
- active tail 有有效文字时进入 final drain；无有效文字时保留最后可见文本并标记待确认。
- AI 修正逐段原位更新，不把正文替换成加载空态。
- 建议、方案和纪要的生成进度只影响右栏/复盘区，不遮挡正文。

### 7.4 会话恢复

- 页面启动时请求非 demo 会话列表。
- 若没有当前 session，自动恢复最近一场包含文字或录音的真实会话。
- 恢复后显示 `已恢复最近会议`，而不是空白待开始页。
- 浏览器刷新无法恢复麦克风 MediaStream 时，显示 `录音连接已中断，已保留截至断开时的文字和录音`，不得伪装为仍在录音。
- 用户仍可通过历史记录切换到其他会议。

## 8. 性能边界

- partial 更新只修改一个 active tail 节点。
- final/revision 不允许生成累计重复段落。
- 30 分钟会议 canonical segment 数量由 endpoint/final 决定，不由 partial 数量决定。
- 视觉段落使用有界合并，避免每次静音产生一行。
- committed document 重建只发生在 final/revision/snapshot，不发生在每个音频 chunk。
- 不新增远端 ASR，不新增默认收费项目，partial 不调用 LLM。

## 9. 验收标准

### 9.1 Reducer

- 累计快照 `A -> AB -> ABC` 的用户可见结果为 `A + B + C`，不是 `A + AB + ABC`。
- `text` 和 `normalized_text` 对同一增量保持一致。
- 100 个同 segment partial 只产生一个 active tail。
- final 将 active tail 原位提交。
- revision 替换 segment，full text 不重复。
- snapshot 与实时投影产生相同 full text。

### 9.2 页面

- 录音任意时刻都能看到从会议开始到当前的完整正文。
- 页面最多一个 active tail。
- 页面最多一个可见结束会议按钮。
- 用户上滚后不会被新 partial 拉回底部。
- 新内容按钮可恢复跟随。
- 停止会议、整理、刷新和历史恢复后正文不消失。
- 最新真实会议能自动恢复。

### 9.3 全链路

- 受控中文音频至少包含三段语音和两次明显停顿。
- 录音期间 committed text 持续增长，active tail 只包含未提交后缀。
- 最终 `canonical_transcript.full_text` 与保存文字稿一致。
- 录音保存及 SHA 校验继续通过。
- AI 建议和 AI 校正继续引用 canonical segment evidence。
- console/network error 为 0。

## 10. 非目标

- 本轮不实现说话人分离。
- 本轮不更换 FunASR 模型。
- 本轮不新增远程收费 ASR。
- 本轮不重做整个右栏或会后复盘信息架构。
- 本轮不承诺刷新后继续原浏览器麦克风采集，只保证已保存内容恢复和状态诚实。
