# PCWEB-130 UI/UX Pro Max Workbench Redesign

> Date: 2026-07-04  
> Decision: DEC-204  
> Scope: Improve the current PC Web MVP workbench presentation without changing product logic, microphone policy, ASR provider policy, or LLM execution policy.

## Trigger

The user requested installing `ui-ux-pro-max-skill` and using it to redesign the display page because the current page looked too rough.

## Skill Installation

The official installer was run:

```text
npx --yes ui-ux-pro-max-cli init --ai codex
```

Observed result:

```text
UI/UX Pro Max installed successfully
Installed folders: .codex
```

The generated `ui-ux-pro-max` skill was also copied to the global Codex skill directory so future Codex restarts can discover it:

```text
~/.codex/skills/ui-ux-pro-max/SKILL.md
```

## Design System Input

The design-system search was run for:

```text
AI meeting copilot developer tool realtime desktop dashboard B2B productivity
```

Relevant design guidance retained:

- dark, technical, precision-oriented surface
- Inter/system UI typography
- micro-interactions, visible focus states, reduced-motion support
- no frozen loading states
- clear status feedback for blocked/safe states
- responsive padding and small-screen wrapping

The generated landing-page pattern was intentionally not adopted because this project is not a marketing page. The implemented direction is a dense desktop workbench.

## Implemented UI Direction

The workbench now uses:

- dark developer-tool shell
- compact brand lockup with vector CSS mark
- grouped toolbar:
  - primary flow: fixture, load, mainline trial
  - simulation flow: Shadow MVP, realistic simulation, long meeting simulation
  - support flow: export and delete
- green primary CTA for `主线试运行`
- red-outline destructive affordance for `删除会话`
- summary status strip for local/no-remote defaults
- dark panel/tile system for readiness, evidence, transcript, report, and feedback areas
- focus-visible ring and reduced-motion guard
- responsive mobile wrapping for toolbar groups and status chips

## Files Changed

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `.codex/skills/ui-ux-pro-max/**`
- `~/.codex/skills/ui-ux-pro-max/**`

## Verification

TDD red:

```text
test_workbench_static_assets_are_served
Result: failed because class="app-shell" did not exist yet
```

Green:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 1 passed, 2 warnings
```

Browser smoke:

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline ASR blocked trial"
```

Visual checks:

- desktop screenshot: `artifacts/tmp/ui_screenshots/workbench-home-dark-v4.png`
- mobile screenshot: `artifacts/tmp/ui_screenshots/workbench-mobile-dark-v4.png`
- mobile layout probe: `scrollWidth == clientWidth`, no horizontal overflow offenders

## Boundaries

This redesign did not:

- access microphone devices
- read user audio
- call remote ASR
- call remote LLM
- change backend product logic
- change ASR quality gate status
- change real microphone readiness status

## Follow-Up

Next UI work should improve information architecture after PCWEB-129 creates the feedback/export closure. The visual system is now good enough to carry the main product flow; the next value step remains product closure, not more surface polish.

