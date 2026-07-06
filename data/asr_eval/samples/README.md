# ASR 评测音频样本

这个目录存放中文技术会议音频样本。

当前仓库不内置真实会议录音，避免泄露隐私。真实样本建议按以下命名：

- `S01-api-review.wav`
- `S02-release-review.wav`
- `S03-incident-review.wav`
- `S04-architecture-review.wav`

采样建议：

- WAV/FLAC 优先。
- 16kHz mono 可用于 ASR；原始分轨音频也要保留。
- 每段样本都要配套 reference 和 annotation。

临时测试可放入空文件或短测试音频，mock provider 不读取音频内容。
