# DRV-034 Public Audio Post-Extraction Evidence Schema Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`ASR quality`、`EvidenceSpan/state/gap`、`pilot`  
> 边界：本计划不授权下载公开音频、不授权解压/抽取/转码公开音频、不读取真实音频或 `.m4a`、不读取 `configs/local/`、不读取 `data/asr_eval/local_samples/`、不运行 ASR、不调用远程 ASR/LLM、不访问麦克风、不运行外部命令。

## 1. 目标

DRV-031 已把公开音频阶段收束为下载前 planned sample manifest 决策：没有真实 archive member path、clip sha256 和 GB 级下载审批时保持 blocked。下一步需要一个“抽样后证据”入口，但这个入口不能变成下载器、解压器、转码器或 ASR runner。

DRV-034 增加 `public_audio_post_extraction_evidence.v1` schema gate。它只接收已经由人工批准流程产生的 evidence JSON，验证来源、授权、clip 区间、expected/observed sha256、observed duration、采样率、声道数、cleanup 状态和 side-effect flags。工具本身不读取任何音频文件，不运行外部命令，不调用 ASR/LLM。

## 2. 范围

新增：

- `tools/public_audio_post_extraction_evidence_schema.py`
- `tests/test_public_audio_post_extraction_evidence_schema.py`

同步：

- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `README.md`

不做：

- 不下载 AliMeeting/AISHELL-4/AISHELL-1。
- 不解压 archive。
- 不裁剪、转码或读取音频。
- 不运行 `ffmpeg`、`afconvert` 或任何外部命令。
- 不运行 FunASR/sherpa/Whisper。
- 不调用远程 ASR/LLM。
- 不访问麦克风或真实用户录音。

## 3. Contract

工具默认输出 schema contract：

- `decision_id=DRV-034`
- `report_mode=public_audio_post_extraction_evidence_schema`
- `schema_version=public_audio_post_extraction_evidence.v1`
- `schema_status=specified_not_executable`
- `execution_boundary=schema_only_no_audio_read_no_download_no_asr`
- all safety flags false

合法 evidence report 必须包含：

- `planned_sample_id`
- `source_id/source_url/source_license/source_snapshot_date`
- `archive_name/archive_member_path`
- `clip_start_seconds/clip_end_seconds/expected_duration_seconds`
- `expected_sha256_after_extract`
- `observed_sha256`
- `observed_duration_seconds`
- `sample_rate_hz`
- `channel_count`
- `container_format`
- `codec`
- `license_citation`
- `cleanup_status`
- `derived_artifact_root`
- no-download/no-extract/no-transcode/no-audio-read/no-ASR/no-remote/no-LLM flags 全 false

合法 report 返回：

- `evidence_report_status=schema_validated_no_audio_access`
- `evidence_report_validation_status=passed`
- `evidence_summary`

阻断条件：

- evidence report path 不在 `artifacts/tmp/public_audio`。
- path 指向 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime` 或 `outputs`。
- side-effect flags 不是 false。
- `observed_sha256` 不是 64 位小写 sha256，或不等于 `expected_sha256_after_extract`。
- `planned_sample_id/source_id` 含本机路径、相对 forbidden-root、反斜杠路径或 `.m4a`。
- source attribution 与批准白名单不一致。

## 4. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_post_extraction_evidence_schema.py \
  -q -p no:cacheprovider
```

结果：`7 failed, 1 warning`。失败原因是 `tools/public_audio_post_extraction_evidence_schema.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_post_extraction_evidence_schema.py \
  -q -p no:cacheprovider
```

结果：`7 passed, 1 warning`。

复审补充覆盖：

- 增加 forbidden report path 覆盖：`configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`。
- 增加 repo-outside path、allowed-root symlink escape 和 non-JSON suffix 预读阻断覆盖。
- 增加 schema 负例覆盖：source attribution mismatch、unsafe archive member path、bad clip window、duration mismatch、invalid sample rate/channel count、invalid cleanup status 和 invalid derived artifact root。

最终 focused gate：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_post_extraction_evidence_schema.py \
  -q -p no:cacheprovider
```

结果：`10 passed, 1 warning`。

## 5. 后续

DRV-034 完成后，公开音频链路仍保持 no-download blocked，除非后续出现用户批准的具体 planned samples 和 post-extraction evidence JSON。下一步最小闭环应转向 `replay -> DRV-033 shadow report draft adapter`，把 PCWEB-110/111 的 replay timeline 映射成真实 shadow-test 报告草稿，但仍不访问麦克风、不读取真实音频、不生成真实反馈。
