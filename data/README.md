# Data

此目录只保存可公开、可复现的脱敏演示数据和评测词表。

```text
data/
├─ web_mvp/fixtures/       # 工作台演示会议输入
└─ asr_eval/glossaries/    # 中文技术术语与热词表
```

以下内容均为本地数据，不得提交：

- `data/local_runtime/`：SQLite、录音、任务和设置
- `data/asr_eval/local_samples/`：本地或用户音频样本
- `data/asr_eval/public_raw/`：下载的公开语料原始包
- 任何包含真实姓名、会议内容、密钥或未获授权音频的文件

新增 fixture 时应使用虚构信息，确保不包含可识别个人或组织的数据。大型模型、数据集和运行产物应放在已忽略目录，并在相应许可证允许的范围内单独分发。
