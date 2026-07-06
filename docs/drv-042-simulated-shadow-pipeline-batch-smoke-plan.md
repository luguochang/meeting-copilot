# DRV-042 Simulated Shadow Pipeline Batch Smoke

> 日期：2026-07-04
> 状态：Implemented
> 目的：把 DRV-041 的单场景模拟链路升级为 5 场景批量自测，固定工程正例和非工程负控的产品价值门。
> 边界：本工具不读取真实音频或 `.m4a`，不访问麦克风，不下载公开音频或模型，不调用远程 ASR/LLM，不运行 Cargo/Tauri，不写 audio chunk，不写导出文件，不读取 `configs/local/`、`data/local_runtime/` 或 `outputs/`。

## 背景

DRV-041 已证明单个 approved ASR event JSON 可以串过 replay、shadow report draft 和 export preview。下一步需要避免只看单个场景造成误判：工程会议应该形成 preview，非工程对照必须保持无候选、无假卡片。

## 决策

在 `tools/simulated_shadow_pipeline_smoke.py` 中新增：

- `build_simulated_shadow_pipeline_batch_smoke(...)`
- CLI flag：`--batch-default-mock-events`
- `DEFAULT_MOCK_SCENARIO_SPECS`

默认批量场景固定为：

| session | expected_kind |
| --- | --- |
| `api-review-001` | `engineering` |
| `architecture-review-001` | `engineering` |
| `incident-review-001` | `engineering` |
| `release-review-001` | `engineering` |
| `non-engineering-control-001` | `negative_control` |

通过条件：

- 所有工程场景 `pipeline_status=simulated_shadow_pipeline_preview_created`，且 `candidate_cards > 0`。
- 非工程对照 `pipeline_status=blocked_by_no_candidate_timeline`，且 `candidate_cards=0`。
- batch 输出 `simulated_shadow_pipeline_batch_passed`。
- batch 输出 `go_evidence_status=not_go_evidence_batch_replay_or_feedback_missing`，明确不能作为真实 Go evidence。

失败条件：

- 任一工程场景未形成 preview。
- 任一负控形成 candidate 或 preview。
- 任一场景 path/event/manifest 被上游 path guard 或 event contract 阻断。

## 验收

TDD 覆盖：

- 工程场景和非工程负控共同通过时，batch 返回 `simulated_shadow_pipeline_batch_passed`。
- 工程场景没有 preview 时，batch 返回 `failed_engineering_preview_or_negative_control`。
- forbidden path 在读取前 blocked。
- CLI `--batch-default-mock-events` 可跑默认 5 场景。

验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py -q -p no:cacheprovider
```

真实本地 artifacts CLI smoke：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events
```

预期摘要：

- `scenario_count=5`
- `engineering_preview_created_count=4`
- `negative_control_blocked_count=1`
- `negative_control_fake_candidate_count=0`
- `artifact_write_status=not_written`

## 非目标

- 不做真实 ASR 质量判断。
- 不从音频生成 event。
- 不下载公开音频。
- 不调用中转站或远程 ASR。
- 不替代真实麦克风 shadow test。

## 后续

DRV-042 完成后，mock 层产品价值门已经有批量自测。下一步不应继续扩展 mock smoke，而应转向 ASR quality exit：本地 FunASR 模型目录、DRV-019 审批、显式远端 ASR 对照，或显式降级试点风险接受。
