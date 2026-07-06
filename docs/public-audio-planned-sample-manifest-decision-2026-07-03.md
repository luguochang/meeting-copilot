# Public Audio Planned Sample Manifest Decision

> 日期：2026-07-03  
> 状态：Accepted  
> 决策 ID：`DRV-031`  
> 边界：本文档不授权下载公开音频、不授权解压/抽取/转码、不授权访问麦克风、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型。

## 1. 结论

公开音频 planned sample manifest 已经收束成机器可测决策：

```text
decision_status=blocked_no_verified_public_sample_manifest
public_audio_stage_status=blocked_no_planned_samples
```

当前不能把 AliMeeting 或 AISHELL-4 继续推进到真实公开音频小样本 ASR。原因不是没有找到公开来源，而是缺少进入 no-download manifest 的必要证据：

- 没有真实 `archive_member_path`。
- 没有 `expected_sha256_after_extract`。
- 没有用户批准 GB 级公开包下载。

因此公开音频阶段按计划保持 blocked，不继续泛搜更多网站，不转向 Bilibili、YouTube、播客、公开视频、公开课、技术大会录播，也不把 MagicHub/MagicData/Common Voice 等 observed-but-not-whitelisted 候选提升为自动执行来源。

## 2. 实现

新增：

- `tools/public_audio_planned_sample_manifest_decision.py`
- `tests/test_public_audio_planned_sample_manifest_decision.py`
- `docs/public-audio-planned-sample-manifest-decision-2026-07-03.md`

默认候选顺序：

1. `alimeeting_openslr_slr119`
   - 官方页：`https://www.openslr.org/119/`
   - 授权：`CC BY-SA 4.0`
   - 首选包：`Eval_Ali.tar.gz`
   - 包体备注：约 `3.42G`
2. `aishell4_openslr_slr111`
   - 官方页：`https://www.openslr.org/111/`
   - 授权：`CC BY-SA 4.0`
   - 首选包：`test.tar.gz`
   - 包体备注：约 `5.2G`

工具只做决策和 schema 校验：

- 默认无 planned samples 文件时返回 `blocked_no_verified_public_sample_manifest`。
- 如果提供合法 planned samples 文件，只返回 `schema_validated_no_download` / `ready_for_manual_download_review`。
- 不生成 `download_command`。
- 不生成 `extract_command`。
- 不生成 `transcode_command`。
- 不调用 ASR。

## 3. TDD 证据

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_planned_sample_manifest_decision.py \
  -q -p no:cacheprovider
```

结果：

```text
4 failed, 1 warning
```

失败原因：`tools/public_audio_planned_sample_manifest_decision.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_planned_sample_manifest_decision.py \
  -q -p no:cacheprovider
```

结果：

```text
4 passed, 1 warning
```

默认 CLI：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py
```

结果摘要：

```text
decision_status=blocked_no_verified_public_sample_manifest
public_audio_stage_status=blocked_no_planned_samples
blocked_reasons=[
  no_verified_archive_member_path,
  no_expected_clip_sha256_after_extract,
  no_user_approval_for_gb_archive_download
]
download_command=null
extract_command=null
transcode_command=null
safe_to_download_now=false
safe_to_extract_now=false
safe_to_transcode_now=false
safe_to_call_asr_now=false
```

## 4. 后续边界

公开音频阶段只有在以下任一条件满足后才继续：

- 用户提供合法 planned samples 文件，字段包含 `sample_id`、`source_id`、`source_url`、`source_license`、`archive_name`、`archive_member_path`、`clip_start_seconds`、`clip_end_seconds`、`expected_duration_seconds`、`expected_sha256_after_extract`、`license_citation`、`cleanup_required`。
- 用户明确批准一次 GB 级公开包下载，并接受 ignored 存储、checksum、post-extraction observed sha256 和清理策略。

否则下一主线应转向：

- ASR quality decision / FunASR 本地模型目录或 DRV-019 模型审批。
- 真实 Tauri no-op run。
- mic adapter contract。

公开音频当前不再作为下一主线继续扩展。
