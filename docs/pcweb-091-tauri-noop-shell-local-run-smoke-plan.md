# PCWEB-091 Tauri No-op Shell Local Run Smoke Plan

> 日期：2026-07-02  
> 状态：已实现并通过 focused、docs、adjacent、pc-web 和 all-local --no-browser 门禁  
> 范围：桌面端从 PCWEB-090 之后进入 Tauri no-op shell local run smoke readiness/report boundary。  
> 强约束：不运行 Cargo/Tauri/package manager，不安装 Rust，不读取 `configs/local/`，不读取真实音频，不启动 ASR worker，不调用远程 ASR/LLM。

## 1. 目标

PCWEB-091 的目标不是运行 Tauri，而是建立未来第一次 Tauri no-op shell local smoke 的受控边界：

```text
PCWEB-082 Tauri static scaffold
  + PCWEB-090 first cargo check execution boundary
  -> PCWEB-091 no-command shell smoke readiness report
```

该报告只说明：当前静态 scaffold 是否已经准备好进入“未来显式批准的 Tauri no-op shell smoke”。即使全部验证通过，也只能返回 `ready_for_explicit_tauri_run_approval`，不能执行命令。

PCWEB-091 新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。报告模式固定为 `readiness_report_only`；即使返回 `ready_for_explicit_tauri_run_approval`，也继续保持 `safe_to_run_tauri_dev_now=false` 和 `safe_to_capture_audio_now=false`。

评审后已补强：package-manager artifacts（`package.json`、`package-lock.json`、`pnpm-lock.yaml`、`yarn.lock`）会阻断 readiness；no-op command function 必须映射到对应 command id；`safe_to_execute_real_action=false`、`captures_audio=false`、`spawns_process=false`、`calls_remote_provider=false` 和 `writes_local_files=false` 等 no-side-effect 字段漂移会阻断 readiness。

## 2. 为什么现在做

PCWEB-090 已经把首次 `cargo check` 收束为手动执行包，但项目还没有真正进入桌面壳运行路径。继续扩 ASR provider、远程 provider 或 fixture-only UI 的收益下降。

PCWEB-091 让主线靠近真实客户端，同时继续保留安全边界：

- 不运行 Tauri。
- 不抓依赖。
- 不生成 lock/build artifacts。
- 不接音频。
- 不读密钥。
- 不调用远程 provider。

## 3. 交付物

- `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`
- `tools/desktop_tauri_noop_shell_run_smoke.py`
- `tests/test_desktop_tauri_noop_shell_run_smoke.py`
- `docs/superpowers/specs/2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke-design.md`
- `docs/superpowers/plans/2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke.md`

同时更新：

- `README.md`
- `code/web_mvp/README.md`
- `code/desktop_tauri/README.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/decision-log.md`
- `docs/project-current-status-2026-07-02.md`
- `docs/project-progress-report-2026-07-02.md`
- `docs/project-stage-status-and-next-work-2026-07-02.md`

## 4. 报告字段

核心字段：

- `pcweb_id=PCWEB-091`
- `policy_name=Desktop Tauri No-op Shell Local Run Smoke`
- `policy_status=tauri_noop_shell_local_run_smoke_policy_only`
- `smoke_boundary_mode=readiness_report_only`
- `accepted_desktop_scaffold_source=pcweb_082_tauri_shell_scaffold`
- `accepted_cargo_check_boundary_source=pcweb_090_first_cargo_check_execution_boundary`
- `tauri_shell_run_status=not_run`
- `external_command_execution_status=not_run`
- `approval_status=explicit_tauri_run_approval_not_recorded`
- `smoke_packet_status=blocked_*` 或 `ready_for_explicit_tauri_run_approval`

固定检查：

- `dev_url=http://127.0.0.1:8765/`
- `frontend_dist=../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static`
- `bundle_active=false`
- `expected_noop_commands=["runtime_get_status","session_prepare","asr_worker_health"]`
- `expected_bridge_command_ids=["runtime.get_status","session.prepare","asr_worker.health"]`
- minimal capability `permissions=["core:default"]`

## 5. 安全边界

所有 `safe_to_*` 仍为 false：

- `safe_to_run_tauri_dev_now`
- `safe_to_run_tauri_build_now`
- `safe_to_run_cargo_check_now`
- `safe_to_run_cargo_build_now`
- `safe_to_spawn_process_now`
- `safe_to_fetch_dependencies_now`
- `safe_to_generate_cargo_lock_now`
- `safe_to_generate_target_dir_now`
- `safe_to_generate_installer_now`
- `safe_to_request_audio_permission_now`
- `safe_to_capture_audio_now`
- `safe_to_start_asr_worker_now`
- `safe_to_read_provider_config_now`
- `safe_to_read_secret_now`
- `safe_to_read_configs_local_now`
- `safe_to_call_remote_provider_now`

## 6. 验收

必须完成：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_shell_run_smoke.py -q -p no:cacheprovider
```

相邻回归：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_shell_run_smoke.py tests/test_desktop_first_cargo_check_execution_boundary.py tests/test_desktop_tauri_scaffold.py tests/test_quality_gate.py -q -p no:cacheprovider
```

文档 gate：

```bash
cd code/web_mvp/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q -p no:cacheprovider
```

质量门禁：

```bash
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

以上命令不得运行 Cargo/Tauri/package manager，不得读取 `configs/local`，不得调用远程 provider。

## 7. 完成后的下一步

PCWEB-091 完成后，下一步进入：

```text
PCWEB-092 desktop native bridge no-op integration tests
```

只有在用户另起显式批准后，才进入真实 `cargo check` 或真实 Tauri shell run。
