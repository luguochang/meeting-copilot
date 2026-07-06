# Audio Source, Simulation, and Real Mic Execution Lock

> 日期：2026-07-04  
> 状态：Accepted execution lock  
> 目的：直接回答“完整计划是否已经写下、转写验证是否由我先通过网上公开音频和模拟完成、真实麦克风会议是否最后由用户验证”。  
> 边界：本文档不授权访问麦克风、不授权读取真实用户录音、不授权读取任何 `.m4a`、不授权读取 `configs/local/`、不授权读取 `data/asr_eval/local_samples/`、不授权读取 `data/local_runtime/` 或 `outputs/`、不授权下载公开音频大包、不授权自动下载 FunASR/ModelScope 模型、不授权调用远程 ASR/LLM。

## 1. 直接结论

完整计划已经写下。当前权威入口是：

- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-mainline-index.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

转写类验证的责任边界固定为：

```text
网上官方公开音频来源复核
  -> no-download planned sample manifest
  -> 本地中文技术会议合成音频 / mock streaming events
  -> approved ASR event replay
  -> EvidenceSpan / state / gap / candidate-card 模拟闭环
  -> ASR quality gate
  -> 用户最终真实麦克风会议 shadow test
```

也就是说，公开音频和模拟转写由我先做；真实麦克风会议由用户在 readiness gate 满足后最终验证。当前不进入真实麦克风，不读取用户录音，不下载公开音频大包。

## 2. 本轮联网复核到的官方音频来源

默认白名单不扩大。继续只保留官方可追溯、授权边界清楚的来源。

| 来源 | 官方链接 | 本轮复核摘要 | 当前用途 | 当前状态 |
| --- | --- | --- | --- | --- |
| AliMeeting / OpenSLR SLR119 | `https://www.openslr.org/119/` | OpenSLR 标识为 Mandarin multi-channel meeting speech corpus，License 为 CC BY-SA 4.0；Eval 包约 3.42G，Test 包约 8.90G | 会议声学、远近场、多人、重叠说话、切句、ASR event contract | `whitelisted_no_download` |
| AISHELL-4 / OpenSLR SLR111 | `https://www.openslr.org/111/` | OpenSLR 标识为 conference scenario Mandarin multi-channel meeting corpus，License 为 CC BY-SA 4.0；test 包约 5.2G | 多人会议、远场、多通道、重叠说话补充验证 | `whitelisted_no_download` |
| AISHELL-1 / OpenSLR SLR33 | `https://www.openslr.org/33/` | OpenSLR 标识为 Mandarin speech corpus，License 为 Apache License v2.0；speech data 包约 15G | 普通话 ASR/runtime sanity check | `whitelisted_no_download_sanity_only` |
| MagicData-RAMC / OpenSLR SLR123 | `https://www.openslr.org/123/` | License 为 CC BY-NC-ND 4.0，包约 15G，且不是会议主集 | 只观察，不进入默认自动评测 | `observed_but_not_whitelisted` |

不进入自动评测的来源仍包括 Bilibili、YouTube、播客、公开课、直播回放、技术大会录播、平台课程音频、授权链不清的公开视频、需要登录或禁止衍生处理的数据、以及依赖平台音频 URL 的语料。

## 3. 为什么现在不下载公开音频

当前要避免两类风险：

- 包体风险：AliMeeting/AISHELL-4/AISHELL-1 的候选包是 GB 级，直接下载会引入存储、清理和等待成本。
- 证据风险：没有具体 `archive_member_path`、clip start/end、expected sha256、license citation 和 cleanup manifest 时，即使下载了也不能形成可复核的小样本证据。

因此公开音频当前只推进到：

```text
official source whitelist
  -> no-download planned sample manifest
  -> future human-approved bounded clip extraction
```

没有 verified bounded clip manifest 前，公开音频阶段必须保持 `blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`。

## 4. 模拟转写怎么继续自测

本地模拟分三层，每层解决不同问题：

1. `perfect transcript lane`：验证产品脑子能不能从标准文字稿里发现工程缺口。
2. `mock ASR lane`：验证 partial/final/revision/error/end_of_stream 事件合同、EvidenceSpan、state/gap 和 candidate-card 链路。
3. `real ASR lane`：等本地 FunASR 模型目录、DRV-019 审批或合法 bounded public clip 就绪后，验证真实 ASR 的中文技术实体、延迟、RTF 和 EvidenceSpan 追溯。

当前可复跑命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py
```

预期：`source_validation_status=passed`，且 `safe_to_download_now=false`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py \
  --source-id alimeeting_openslr_slr119 \
  --target-root artifacts/tmp/public_audio
```

预期：`blocked_no_planned_samples`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py
```

预期：`blocked_no_verified_public_sample_manifest`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py \
  --batch-default-mock-events
```

预期：4 个工程场景生成 simulated shadow preview，1 个非工程 control 被阻断，`negative_control_fake_candidate_count=0`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py
```

预期：当前默认仍为 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。

## 5. 真实麦克风会议进入条件

真实麦克风会议只由用户最终执行。进入前必须满足：

- desktop/Tauri runtime 可运行并完成必要 no-op/IPC evidence。
- mic adapter 支持用户显式 start/pause/resume/stop/delete 的边界。
- ASR worker 能输出统一 ASR event。
- UI 能展示 transcript、EvidenceSpan、state/gap、candidate/card 和 readiness blockers。
- ASR quality gate 不再阻塞，或用户显式接受一次降级 shadow-test 风险。
- 导出/反馈链路能形成 transcript、ASR metrics、timeline、card feedback 和 Go/Pivot/Stop 报告。

当前真实麦克风状态仍应是 `blocked_not_ready_for_user_real_mic_shadow_test`，主因是 ASR quality 未退出。

## 6. 下一步执行锁

后续不再新增泛化评测循环。默认只允许三个出口：

1. ASR quality exit：提供已验证 FunASR 本地模型目录、批准 DRV-019 手动模型下载、或提供可校验的本地 FunASR synthetic smoke batch artifact。
2. Public audio bounded clip manifest：只针对 AliMeeting/AISHELL-4 做 3-5 个可复核 clip 的 no-download manifest；没有 archive member path 和 checksum 时继续 blocked。
3. Explicit degraded pilot：用户显式接受一次 ASR 质量降级风险后，只进入 timing/feedback shadow-test 准备；该路径不算 ASR quality Go evidence。

当前不再把“继续找更多音频网站”作为主线动作。更重要的是把现有白名单来源、模拟链路、ASR quality gate 和真实麦克风 readiness 串成可判断的 Go / Pivot / Stop。

## 7. DEC-188 二次审查后的执行锁补充

本轮两路只读 Agent 审查再次确认：完整计划已经写下，转写类验证先由我完成，用户最终再做真实麦克风会议验证。当前不需要新增总控计划。

补充锁定：

- PCWEB-125 `Mac Local Shadow MVP synthetic demo closure` 已完成，后续不能再把它当作未完成开发主线。
- Public audio bounded manifest 只是条件分支。没有真实 `archive_member_path`、clip start/end、expected sha256、license citation、cleanup strategy 和 GB 级公开包下载审批前，必须保持 blocked，不下载、不抽取、不转码、不喂 ASR。
- 默认主线回到 ASR quality exit：优先已验证本地 FunASR 模型目录/cache；其次 DRV-019 手动模型下载审批；或者合法 DRV-046 batch evidence assembly；最后才是用户显式 degraded pilot 风险接受。
- Degraded pilot 只验证 timing/feedback，不算 ASR quality Go evidence，也不代表真实麦克风会议已经 ready。
