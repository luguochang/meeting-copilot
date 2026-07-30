# 本地模型包清单

这里保存模型包的可追溯 manifest，不保存模型权重本体。

每个 manifest 应至少说明：

- 模型用途和语言范围。
- 上游来源、版本或 revision。
- 必需文件和 hash。
- 许可证和再分发状态。
- packaged runtime 需要的相对路径。

模型本体放在本机或安装包的受控 runtime 目录，不能进入 Git。没有通过 manifest、hash、许可证和再分发检查的模型，不得写成 packaged release ready。
