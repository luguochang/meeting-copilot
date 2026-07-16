# Readiness Documentation Index

> Updated: 2026-07-15
>
> Scope: documentation classification only. No source document or evidence artifact is moved by this index.
>
> Current release posture: `L0 功能原型`. The repository is not documented here as released, production-ready, Mac Alpha, or publicly deliverable.

## How to use this index

Use the classes in this order when documents disagree:

1. **Current** defines the product intent, current implementation status, active maturity plan, capability traceability, and accepted decisions.
2. **Superseded** documents must not be used to establish current completion or release readiness, even when their filenames contain "final", "delivery", or "acceptance".
3. **Evidence-only** documents and artifacts prove only the scoped run, environment, date, and assertions they record. They do not independently change the current maturity level.
4. **Historical** plans explain prior intent and sequencing. They are not active backlogs and do not override the current maturity plan.

Within the Current set, use `current-mainline-index.md` for the latest status and blockers, `production-maturity-architecture-and-execution-plan-2026-07-14.md` for active phase exits, and `decision-log.md` for the latest accepted or explicitly superseding decision. The capability matrix remains the requirement-to-test map; an old status cell in that matrix does not override the newer mainline index.

## Current

These are the current authority set. Adding another document to this class requires updating this index and identifying which authority it owns.

| Authority | Document | Use |
|---|---|---|
| Product requirements (PRD) | [`docs/product-requirements.md`](../product-requirements.md) | Canonical product problem, target users, value proposition, non-goals, and product requirements. |
| Current status | [`docs/current-mainline-index.md`](../current-mainline-index.md) | Latest implementation facts, maturity level, current blockers, and pointers to fresh evidence. This is the first status entry point. |
| Active maturity plan | [`docs/production-maturity-architecture-and-execution-plan-2026-07-14.md`](../production-maturity-architecture-and-execution-plan-2026-07-14.md) | Active architecture, milestone order, phase exit criteria, and the boundary between Browser Vertical Alpha, recoverable runtime, Mac Internal Alpha, and later pilot work. |
| Capability / traceability matrix | [`docs/requirements-traceability-matrix.md`](../requirements-traceability-matrix.md) | Requirement IDs, capability coverage, acceptance criteria, and test/evidence mappings. Status conflicts defer to the current mainline index. |
| Decision record | [`docs/decision-log.md`](../decision-log.md) | Chronological product, architecture, cost, privacy, and readiness decisions. Newer accepted decisions and explicit supersession take precedence over older entries. |

`PRD对齐检查清单.md` is not the canonical PRD. It is classified below as Superseded because its checked completion claims predate the current maturity baseline.

## Historical

Historical documents are retained in place for rationale and auditability. They may describe work that was useful at the time, but they are not current status, current scope, or release authorization.

### Readiness, recovery, and completion plans dated 2026-07-13 or earlier

The following high-signal plans and checklists are Historical:

- [`docs/project-release-readiness-reset-2026-07-05.md`](../project-release-readiness-reset-2026-07-05.md)
- [`docs/production-mainline-recovery-plan-2026-07-07.md`](../production-mainline-recovery-plan-2026-07-07.md)
- [`docs/workbench-product-mainline-audit-and-fix-plan-2026-07-07.md`](../workbench-product-mainline-audit-and-fix-plan-2026-07-07.md)
- [`docs/current-status-and-p0-execution-plan-2026-07-08.md`](../current-status-and-p0-execution-plan-2026-07-08.md)
- [`docs/mainline-production-audit-and-recovery-plan-2026-07-08.md`](../mainline-production-audit-and-recovery-plan-2026-07-08.md)
- [`docs/p0-mainline-recovery-execution-plan-2026-07-08.md`](../p0-mainline-recovery-execution-plan-2026-07-08.md)
- [`docs/p0-product-mainline-recovery-checklist-2026-07-08.md`](../p0-product-mainline-recovery-checklist-2026-07-08.md)
- [`docs/p0-real-product-mainline-plan-2026-07-08.md`](../p0-real-product-mainline-plan-2026-07-08.md)
- [`docs/meeting-copilot-completion-target-and-selftest-plan-2026-07-08.md`](../meeting-copilot-completion-target-and-selftest-plan-2026-07-08.md)
- [`docs/full-chain-completion-implementation-plan-2026-07-09.md`](../full-chain-completion-implementation-plan-2026-07-09.md)
- [`docs/p0-p2-full-completion-execution-plan-2026-07-09.md`](../p0-p2-full-completion-execution-plan-2026-07-09.md)
- [`docs/p0-p2-mainline-closure-checklist-2026-07-09.md`](../p0-p2-mainline-closure-checklist-2026-07-09.md)
- [`docs/superpowers/plans/2026-07-11-realtime-transcript-focus-implementation-plan.md`](../superpowers/plans/2026-07-11-realtime-transcript-focus-implementation-plan.md)
- [`docs/superpowers/plans/2026-07-12-canonical-transcript-implementation-plan.md`](../superpowers/plans/2026-07-12-canonical-transcript-implementation-plan.md)
- [`docs/superpowers/plans/2026-07-12-v2-recovery-implementation-plan.md`](../superpowers/plans/2026-07-12-v2-recovery-implementation-plan.md)
- [`docs/v2-ready-gate-followup-2026-07-13.md`](../v2-ready-gate-followup-2026-07-13.md)

This classification also applies to the older `docs/pcweb-*-readiness-*-plan.md`, `docs/drv-*-plan.md`, `docs/superpowers/plans/2026-07-01*` through `2026-07-12*`, and other readiness/recovery/completion plan files whose dated baseline is 2026-07-13 or earlier. Their detailed contracts remain useful historical input, but unfinished checkboxes are not automatically current work and completed checkboxes are not current release evidence.

Scoped platform plans such as `docs/desktop-mac-mvp-plan.md`, `docs/desktop-windows-compatibility-plan.md`, and `docs/mobile-app-future-plan.md` are also non-authoritative supporting plans. Current milestone order and release boundaries come from the active production maturity plan.

## Evidence-only

Evidence-only material is immutable input to a status decision. It must be read with its recorded date, provider mode, environment, fixture or microphone source, cost boundary, and stated limitations.

### Markdown reports

Representative evidence reports include:

- [`docs/real-mic-remote-mainline-report-2026-07-14.md`](../real-mic-remote-mainline-report-2026-07-14.md)
- [`docs/mainline-evidence-fix-report-2026-07-13.md`](../mainline-evidence-fix-report-2026-07-13.md)
- [`docs/mainline-recovery-report-2026-07-13.md`](../mainline-recovery-report-2026-07-13.md)
- [`docs/v2-funasr-real-mic-evidence-2026-07-13.md`](../v2-funasr-real-mic-evidence-2026-07-13.md)
- [`docs/asr-mainline-quality-batch-report-2026-07-10.md`](../asr-mainline-quality-batch-report-2026-07-10.md)
- [`docs/public-chinese-asr-baseline-report-2026-07-10.md`](../public-chinese-asr-baseline-report-2026-07-10.md)
- [`docs/real-mic-workbench-mainline-report-2026-07-10.md`](../real-mic-workbench-mainline-report-2026-07-10.md)
- [`docs/pc-workbench-full-chain-selftest-report-2026-07-09.md`](../pc-workbench-full-chain-selftest-report-2026-07-09.md)
- [`docs/pc-workbench-production-acceptance-report-2026-07-09.md`](../pc-workbench-production-acceptance-report-2026-07-09.md)
- [`docs/workbench-visual-acceptance-report-2026-07-09.md`](../workbench-visual-acceptance-report-2026-07-09.md)
- [`docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md`](../p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md)
- [`docs/mainline-p0-recovery-selftest-report-2026-07-08.md`](../mainline-p0-recovery-selftest-report-2026-07-08.md)
- [`docs/p0-no-mic-simulated-realtime-selftest-report-2026-07-08.md`](../p0-no-mic-simulated-realtime-selftest-report-2026-07-08.md)
- [`docs/p0-real-mic-recorded-realtime-selftest-report-2026-07-08.md`](../p0-real-mic-recorded-realtime-selftest-report-2026-07-08.md)

Unless a report is explicitly listed as Superseded below, files under `docs/` named `*-report-*.md`, `*-selftest-*.md`, `*-result-*.md`, or `*-audit-*.md` are Evidence-only. Words such as `go`, `acceptance`, or `production` inside a scoped report describe that report's gate and do not establish repository-wide release readiness.

### Machine evidence and screenshots

The following path classes are Evidence-only:

- `artifacts/tmp/**/report.json`, `artifacts/tmp/**/summary.json`, and other run manifests under `artifacts/tmp/`.
- `artifacts/tmp/ui_screenshots/**`, `artifacts/tmp/acceptance/**`, `artifacts/tmp/browser_live_mic/**`, and `artifacts/tmp/v2_recovery_process/**`.
- Root screenshots `screenshot_initial.png`, `screenshot_history.png`, `screenshot_settings.png`, and `screenshot_final.png`.
- Generated ASR evaluation reports under `artifacts/tmp/asr_reports/**` and `data/asr_eval/**`.

Screenshots prove visible state at capture time only. JSON evidence proves only the fields and provenance recorded in that artifact. Neither category is a release announcement.

## Superseded

The following documents contain broad "all completed", "production delivery", "final acceptance", or equivalent readiness claims that conflict with the Current `L0 功能原型` baseline. They remain in place as historical records, but their completion ratings and delivery conclusions are superseded:

- [`README_交付成果.md`](../../README_交付成果.md)
- [`交付清单.md`](../../交付清单.md)
- [`PRD对齐检查清单.md`](../../PRD对齐检查清单.md)
- [`全阶段改进完成报告.md`](../../全阶段改进完成报告.md)
- [`修复完成报告.md`](../../修复完成报告.md)
- [`完整质量保证最终报告.md`](../../完整质量保证最终报告.md)
- [`最终交付总结.md`](../../最终交付总结.md)
- [`最终工作总结.md`](../../最终工作总结.md)
- [`最终质量验收报告.md`](../../最终质量验收报告.md)
- [`项目完成确认.md`](../../项目完成确认.md)

The following rules apply to any similar document not explicitly listed:

- A filename containing `最终`, `交付`, `完成确认`, or `all completed` does not make the document current.
- Claims such as "全部完成", "生产交付标准", "可交付使用", "production-ready", or "已发布" are superseded unless the Current authority set is deliberately updated to the same conclusion with satisfied phase exits and linked fresh evidence.
- A scoped phase-completion document may be Historical or Evidence-only rather than Superseded, but it still cannot be generalized into whole-product completion.
- Nothing in this index changes the current no-release posture or authorizes a public-release claim.

## Maintenance rules

When adding readiness documentation:

1. Update an existing Current authority when its ownership matches; avoid creating another status summary.
2. Put run outputs in the existing evidence locations and link them from the current mainline index; do not promote the report itself to Current.
3. Date retired plans and classify them as Historical instead of deleting or rewriting their original conclusions.
4. Mark a broad readiness claim Superseded when a newer authority lowers or changes the maturity conclusion.
5. Do not physically move source documents or artifacts merely to match this logical index; preserve paths used by tests, reports, and prior decisions.
