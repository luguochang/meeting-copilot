# 数据与素材目录

本目录只保存可追溯的测试素材、参考文本、标注和 manifest，不保存用户真实会议数据。

## 分类

- `asr_eval/`：中文 ASR 评测数据。`samples/` 是受控音频，`references/` 是参考文本，`annotations/` 是标注，`glossaries/` 是中文技术词表，`manifests/` 是运行清单。
- `web_mvp/fixtures/`：前端和 API 的确定性状态 fixture，例如发布评审、API 评审和降级场景。
- `product_value_gate/`：实时建议和产品价值链路的触发场景数据。

大模型、FunASR、VAD、CAM++ 和 FFmpeg 等重型资源不直接提交到仓库；仓库只提交版本、hash、许可证和资源来源 manifest。真实会议录音、SQLite、诊断包、截图和临时评测输出必须写入被忽略的 `artifacts/` 或应用本地数据目录。
