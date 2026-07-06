# 中文技术语义与术语规范

> 日期：2026-06-18  
> 目的：定义中文技术会议中的术语、热词、归一化和修正边界。

## 1. 为什么这是核心能力

中文技术会议不是普通中文口语。会议中经常出现：

- 中文、英文、缩写混排。
- 服务名、字段名、接口路径、错误码。
- 数字、百分比、阈值、版本号。
- 内部项目代号和团队黑话。

如果这些实体识别错，实时建议和会后纪要都会失去可信度。

## 2. 实体类型

MVP 支持以下实体类型：

- service：服务名，例如 `user-service`。
- endpoint：接口路径和 method，例如 `POST /api/v1/orders`。
- field：字段名，例如 `trace_id`。
- table：表名。
- metric：指标名，例如 `P99`、`错误率`、`QPS`。
- error_code：错误码，例如 `HTTP 429`、`E1003`。
- component：组件，例如 Redis、Kafka、K8s。
- dependency：调用方、下游、上游、网关。
- person：人名或昵称。
- team：团队名。
- threshold：阈值，例如 `0.1%`、`千分之一`。
- date：日期或相对时间，例如 `下周三`。

## 3. 术语表格式

```json
{
  "project": "payment",
  "version": 1,
  "terms": [
    {
      "type": "field",
      "canonical": "trace_id",
      "aliases": ["trace id", "TraceID", "链路 ID"],
      "description": "链路追踪字段"
    },
    {
      "type": "service",
      "canonical": "payment-gateway",
      "aliases": ["支付网关", "gateway"],
      "description": "支付入口服务"
    }
  ]
}
```

规则：

- canonical 必须稳定。
- aliases 可用于 ASR 热词和后处理。
- 术语表按项目隔离。
- 用户纠错可进入候选术语，但默认不自动污染正式术语表。

## 4. 归一化规则

允许归一化：

- 标点和断句。
- 多余空格。
- 全角/半角。
- 明确等价的大小写。
- 数字表达，例如 `百分之十` -> `10%`。
- 术语别名，例如 `trace id` -> `trace_id`。

不允许归一化：

- 将不确定词改成确定技术实体。
- 将候选决策改写为正式决策。
- 将“可能”“大概”“先看看”改成承诺。
- 为缺失 owner、deadline、阈值进行猜测。

## 5. Raw 与 Normalized

必须同时保存：

- raw transcript：ASR 原始输出。
- normalized transcript：可读性修正版本。
- correction diff：修正来源和置信度。

下游使用原则：

- 证据引用优先展示 raw quote，可附 normalized。
- 技术实体使用 normalized。
- 强建议必须能回到 raw 证据。

## 6. 热词策略

会前：

- 用户选择项目词库。
- 用户可添加临时热词。
- 系统展示本场将使用的热词摘要。

会中：

- ASR provider 支持热词时注入。
- 不支持热词时使用后处理实体归一化。
- 术语置信度低时不触发强建议。

会后：

- 用户纠正实体。
- 纠正项进入候选词库。
- 下次会议可选择启用。

## 7. LLM 修正边界

LLM 可以：

- 修正明显标点、断句、中英混排。
- 按术语表规范实体。
- 标记可能的 ASR 错字。

LLM 不可以：

- 发明未出现的实体。
- 补齐缺失的 owner/deadline/阈值。
- 删除不利于结论的原始片段。
- 把不确定表达改成确定承诺。

