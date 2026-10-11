"""A disclosed, device-free continuous acceptance; plan only by default.

8771 is a separate real application with a test-only atomic admission ceiling.
8791 is a new upstream boundary; its ledger is never reset or reused. Semantic
state and automatic contributions come only from the normal global timer.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx

import long_soak_provider_proxy as proxy
import participation_value_acceptance as value
import workbench_server
from meeting_copilot_web_mvp.application_schema import APPLICATION_SCHEMA_VERSION as DB_SCHEMA_VERSION

ROOT = value.ROOT
DEFAULT_RUN_DIR = ROOT / "artifacts/realtime-participation-20261007/continuous-r01"
DEFAULT_PROFILE = "legacy-continuous"
APP_PORT, PROXY_PORT = 8771, 8791
LIMITS = {"discussion_state": 4, "automatic": 2, "manual": 3}
HOST_LIMIT, UPSTREAM_LIMIT = 9, 14
SCOPE_PREFIX = "participation_continuous_"
BOUNDARY_SCHEMA = "participation_continuous_boundary.v1"
TERMINALS = {"succeeded", "failed", "cancelled"}
STOP_CODES = frozenset({"preexisting_runtime", "preflight_boundary_mismatch", "http_operation_failed",
    "runtime_source_changed", "budget_boundary_activated", "own_job_failed", "overlap_missed",
    "observation_timeout", "material_conversion_failed", "material_conversion_timeout",
    "save_changed_active_scope", "automatic_publication_invalid", "manual_publication_invalid",
    "policy_change_withdrew_card", "transport_failure", "unclassified"})
STOP_CODES |= {"fixture_changed", "profile_boundary_mismatch", "ui_ready_timeout", "dom_receipt_timeout",
    "unexpected_lane", "first_turn_efficiency_failed", "publication_contract_failed", "material_contract_failed"}
PREFLIGHT_CHECK_KEYS = frozenset({"app_identity", "proxy_identity", "provider_config", "health", "schema", "fresh_budget"})


class AcceptanceStop(RuntimeError):
    def __init__(self, code: str, message: str, *, checks: dict | None = None, http_status: int | None = None):
        if code not in STOP_CODES:
            raise ValueError("acceptance stop code is not allowed")
        super().__init__(message)
        self.code = code
        self.checks = {key: bool(value) for key, value in (checks or {}).items() if key in PREFLIGHT_CHECK_KEYS}
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
INPUTS = (
    "用户付钱后要立即看见权益，短信通知可以稍后送达。当前候选是把发权益和短信放在同一个同步流程，任何一步失败就整体重试。我们还没有讨论这种整体重试是否会影响已经生效的权益。付款是否成功的页面展示，还需要和实际权益生效的结果一致。用户可能会重复打开购买结果页面，当前还没有明确短信失败时对页面和权益状态应如何处理。",
    "刚才提出的通知失败不要重新发权益已经讨论过，大家同意短信和权益分开处理，这个问题已回答，不要再重复提。现在继续讨论付款接口超时：支付服务可能已经完成扣款，但调用方还没收到结果。当前候选是由调用方查询支付状态再决定后续动作，还没有确定查询暂时不可用时页面如何提示、是否允许用户重新付款。需求补充资料说明了这轮讨论的边界，尚未形成最终选择。",
    "付款链路的讨论先暂停，这不是已经完成开发，也不是前面方案已正式采用。现在换一个独立议题：新员工第一次登录的欢迎页面，页面文案和帮助入口需要清楚，让用户知道从哪里开始。当前只讨论欢迎页如何介绍功能，不讨论支付超时、短信补发或权益重试。欢迎页候选是先给简短说明再提供按需展开的帮助，是否保留一个可跳过的引导入口尚未决定。",
)
MATERIAL_V1 = "# 付款超时需求 v1\n付款接口超时后先查询支付结果，查询暂时不可用时显示待确认，禁止直接提示支付失败。\n短信发送失败不得重新发放权益。\n本轮候选允许调用方在确认未扣款后重新付款，是否采用还需会议讨论。\n"
MATERIAL_V2 = "# 付款超时需求 v2\n付款接口超时后先查询支付结果，查询暂时不可用时显示待确认，禁止直接提示支付失败。\n短信发送失败不得重新发放权益。\n本轮新增约束：即使确认未扣款，调用方也不得自动重新付款，必须由用户重新确认付款。\n"


def plan(run_dir: Path) -> dict:
    return {"schema_version": "participation_continuous_acceptance.v1", "executed": False,
        "run_dir": str(run_dir), "runtime": str(run_dir / "runtime"),
        "app_url": f"http://127.0.0.1:{APP_PORT}", "proxy_url": f"http://127.0.0.1:{PROXY_PORT}",
        "budget": {"host_jobs": HOST_LIMIT, "state": 4, "automatic": 2, "manual": 3,
            "forwarded_upstream_posts": UPSTREAM_LIMIT, "attempts_per_host_job": 1, "retries": 0},
        "instrumented_test_wrapper": {"atomic_admission_ceiling": True, "manual_max_attempts": 1,
            "production_manual_retry_policy_covered": False, "activation_is_failure": True},
        "timer_gates_overridden": False, "direct_state_or_automatic_admission": False,
        "semantic_fixture_applied": False, "clock_advanced": False, "ttl_shortened": False,
        "audio_capture": False, "audio_asr": False, "input": "public synthetic canonical text + disclosed mock live mirror",
        "production_ports_untouched": [8767, 8768], "old_proxy_ledger_untouched": True,
        "application_schema_version": DB_SCHEMA_VERSION,
        "stages": [
            {"id": "initial_understanding", "input_characters": len(INPUTS[0]), "goal_required": False,
                "documents_required": False, "wait": "natural state1 then automatic1, capture current inside TTL"},
            {"id": "responded_and_inflight_manual", "input_characters": len(INPUTS[1]),
                "wait": "natural state2 then automatic2 observed Provider-in-flight; one manual response",
                "overlap_missed": "uncovered; stop without manufacturing another automatic task"},
            {"id": "material_version_change", "operations": "save v2 while v1 stays enabled; explicitly enable v2",
                "wait": "natural state3, manual compare; verify retraction and old historical sources",
                "cost_control": "preparation policy off only after actual second-auto overlap was observed"},
            {"id": "new_topic", "input_characters": len(INPUTS[2]), "wait": "natural state4 then manual freeform",
                "verify": "new topic/basis, old questions isolated, no old card revival"}],
        "evaluation_limits": "One continuous synthetic text scenario; not ASR, 30-minute semantic soak, or latency percentile proof.",
        "inspection_seconds": 15, "manual_value_review": "pending", "proxy_credentials": "environment only, never report/arguments"}


def fresh_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write("\n")


class ReportFileSystemError(RuntimeError):
    """Fixed metadata diagnostics without the OS message, path or report body."""
    def __init__(self, phase: str, error: OSError):
        if phase not in {"report_temporary_write", "report_atomic_replace", "report_failure_record_write"}:
            raise ValueError("report filesystem phase is not allowed")
        self.diagnostic = {"code": "report_filesystem_failure", "phase": phase,
            **{key: value if type(value) is int and 0 <= value <= 2**31 - 1 else None
               for key in ("errno", "winerror") for value in (getattr(error, key, None),)}}
        super().__init__("acceptance report filesystem operation failed")


def _report_filesystem_error(phase: str, error: OSError) -> ReportFileSystemError:
    failure = ReportFileSystemError(phase, error)
    print(json.dumps(failure.diagnostic), flush=True)
    return failure


def _record_report_failure(run_dir: Path, failure: ReportFileSystemError, report: dict) -> None:
    report["automated_contract_passed"] = False
    diagnostic = dict(failure.diagnostic)
    diagnostic["code"] = ("report_filesystem_failure_after_scope_close"
        if report.get("scope_closed_without_paid_postprocessing") is True
        else "report_filesystem_failure_scope_close_unconfirmed")
    try:
        # A fresh fixed-basename sidecar preserves the first terminal failure;
        # the locked old report and its original bytes are never overwritten.
        with (run_dir / "report-filesystem-failure.json").open("x", encoding="utf-8") as output:
            json.dump(diagnostic, output)
            output.write("\n")
    except OSError as exc:
        _report_filesystem_error("report_failure_record_write", exc)


def update_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise _report_filesystem_error("report_temporary_write", exc) from None
    deadline = time.monotonic() + 0.120
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except OSError as exc:
            error = exc
            winerror = getattr(exc, "winerror", None)
            remaining = deadline - time.monotonic()
            if os.name != "nt" or type(winerror) is not int or winerror not in {5, 32, 33} or attempt == 4 or remaining <= 0:
                break
            time.sleep(min(0.020, remaining))
            if time.monotonic() >= deadline:
                break
    # Retry only the metadata replacement: its bytes and all business actions
    # were produced once. Persistent permissions/locks still fail atomically.
    raise _report_filesystem_error("report_atomic_replace", error) from None


class ContinuousAttemptBudget(proxy.AttemptBudget):
    """New scope only; the old soak budget remains capped at twelve."""
    def __post_init__(self):
        if type(self.limit) is not int or self.limit != UPSTREAM_LIMIT:
            raise ValueError("continuous proxy requires exactly fourteen attempts")


def serve_proxy(run_dir: Path) -> None:
    ledger = run_dir / "proxy-ledger.json"
    if ledger.exists():
        raise ValueError("proxy ledger must be fresh; an old budget cannot be reset")
    budget = ContinuousAttemptBudget(limit=UPSTREAM_LIMIT, report=ledger)
    server = proxy.make_server(port=PROXY_PORT,
        upstream_base=os.environ.get("MEETING_CONTINUOUS_UPSTREAM_BASE_URL", ""),
        upstream_key=os.environ.get("MEETING_CONTINUOUS_UPSTREAM_API_KEY", ""),
        proxy_token=os.environ.get("MEETING_CONTINUOUS_PROXY_TOKEN", ""), budget=budget,
        upstream_httpx_options=proxy.native_provider_httpx_options(
            explicit_proxy_url=os.environ.get("MEETING_CONTINUOUS_UPSTREAM_PROXY_URL", "")))
    # Exclusive initial ledger creation also blocks concurrent restart/reset.
    fresh_json(ledger, budget.snapshot())
    identity = {"schema_version": BOUNDARY_SCHEMA, "role": "proxy", "port": PROXY_PORT,
        "pid": os.getpid(), "process_start_marker": workbench_server.process_start_marker(os.getpid()),
        "ledger": str(ledger), "maximum_forward_attempts": UPSTREAM_LIMIT, "headers_or_bodies_logged": False}
    fresh_json(run_dir / "proxy-identity.json", identity)
    print(json.dumps(identity), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()


def lane(job: dict) -> str | None:
    if job.get("kind") != "intelligence":
        return None
    metadata = job.get("request_context") or {}
    if metadata.get("task_mode") == "discussion_state":
        return "discussion_state"
    if job.get("trigger_type") == "discussion_update":
        return "automatic"
    if job.get("trigger_type") == "user_request" and metadata.get("profile") == "participation":
        return "manual"
    return None


def host_budget(jobs: list[dict]) -> dict:
    counts = {kind: sum(lane(job) == kind for job in jobs) for kind in LIMITS}
    return {**counts, "host_jobs": sum(counts.values()),
        "host_attempts": sum(int(job.get("attempts") or 0) for job in jobs if lane(job)),
        "unrelated_started_jobs": [job["id"] for job in jobs if not lane(job) and int(job.get("attempts") or 0)],
        "retried_jobs": [job["id"] for job in jobs if int(job.get("attempts") or 0) > 1]}


def over_budget(counts: dict) -> bool:
    return bool(counts["host_jobs"] > HOST_LIMIT or any(counts[key] > limit for key, limit in LIMITS.items())
        or counts["unrelated_started_jobs"] or counts["retried_jobs"])


def guarded_persistence_type(base):
    """Wrap actual admission atomically, rolling back only an excess new job."""
    class GuardedPersistence(base):
        @contextmanager
        def _write_transaction(self):
            with self._lock:
                if self._conn.in_transaction:
                    yield
                else:
                    with super()._write_transaction():
                        yield

        def _bounded_admit(self, method, kwargs, *, manual=False):
            mid = str(kwargs.get("meeting_id") or "")
            if not mid.startswith(SCOPE_PREFIX):
                raise RuntimeError("continuous acceptance requires its new isolated scope")
            with self._write_transaction():
                prior = {job["id"] for job in self.list_jobs(meeting_id=mid)}
                self._conn.execute("SAVEPOINT continuous_admission")
                try:
                    result = method(**kwargs)
                    jobs = self.list_jobs(meeting_id=mid)
                    new_ids = [job["id"] for job in jobs if job["id"] not in prior and lane(job)]
                    if new_ids and over_budget(host_budget(jobs)):
                        self._conn.execute("ROLLBACK TO continuous_admission")
                        self._conn.execute("RELEASE continuous_admission")
                        self.acceptance_guard_activations = getattr(self, "acceptance_guard_activations", 0) + 1
                        if manual:
                            raise RuntimeError("continuous host admission budget exhausted")
                        return None
                    if new_ids:
                        self._conn.executemany("UPDATE jobs SET max_attempts=1 WHERE id=?", [(item,) for item in new_ids])
                        result = self.get_job(result["id"]) if result else result
                    self._conn.execute("RELEASE continuous_admission")
                    return result
                except BaseException:
                    # A released budget savepoint has no pending writes. Other
                    # failures keep the original transaction's rollback behavior.
                    if self._conn.in_transaction:
                        try:
                            self._conn.execute("ROLLBACK TO continuous_admission")
                            self._conn.execute("RELEASE continuous_admission")
                        except sqlite3.OperationalError:
                            pass
                    raise

        def enqueue_discussion_state(self, **kwargs):
            return self._bounded_admit(super().enqueue_discussion_state, kwargs)

        def enqueue_discussion_update(self, **kwargs):
            return self._bounded_admit(super().enqueue_discussion_update, kwargs)

        def enqueue_user_coach_request(self, **kwargs):
            return self._bounded_admit(super().enqueue_user_coach_request, kwargs, manual=True)
    return GuardedPersistence


def build_app(run_dir: Path, *, automatic_enabled: bool = True, profile: str = DEFAULT_PROFILE,
              fixture_sha256: str | None = None):
    """Real application and model transport; only isolation/budget instrumentation."""
    data_dir = (run_dir / "runtime").resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("continuous app requires a fresh empty runtime")
    token = os.environ.get("MEETING_CONTINUOUS_PROXY_TOKEN", "")
    if not token:
        raise ValueError("continuous proxy token is required in environment")
    os.environ.update({"LLM_GATEWAY_BASE_URL": f"http://127.0.0.1:{PROXY_PORT}/v1",
        "LLM_GATEWAY_API_KEY": token, "LLM_GATEWAY_MODEL": "gpt-6-sol", "LLM_GATEWAY_REALTIME_MODEL": "gpt-6-sol",
        "LLM_GATEWAY_CORRECTION_MODEL": "gpt-6-sol", "LLM_GATEWAY_IS_MOCK": "0", "LLM_CORRECTION_PRICING_MODE": "unmetered",
        "LLM_GATEWAY_API_STYLE": "responses", "LLM_GATEWAY_TIMEOUT_SECONDS": "25",
        "MEETING_COPILOT_DESKTOP_RUNTIME": "0",
        "MEETING_COPILOT_DATA_DIR": str(data_dir), "MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED": "1" if automatic_enabled else "0",
        "MEETING_COPILOT_DISCUSSION_STATE_ENABLED": "1", "MEETING_COPILOT_REALTIME_COACH_RUNTIME": "pi",
        "MEETING_COPILOT_REALTIME_COACH_ENABLED": "1",
        "MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST": "0", "MEETING_COPILOT_PI_BRIDGE_PREWARM": "1",
        "MEETING_COPILOT_REALTIME_REFINER_POLICY": "on_demand", "MEETING_COPILOT_LOCAL_API_TOKEN": ""})
    # app module construction reads MEETING_COPILOT_DATA_DIR at import time.
    # It must see the isolated directory and proxy before it is imported.
    from meeting_copilot_web_mvp import app as app_module
    # Explicit local test config prevents an inherited private .env fallback.
    app_module.llm_service.REPO_ENV_FILE = run_dir / "not-used.env"
    app_module.llm_service.clear_runtime_config()
    original = app_module.V2Persistence
    app_module.V2Persistence = guarded_persistence_type(original)
    try:
        application = app_module.create_app(data_dir=data_dir, semantic_projection_mode="llm_first", prewarm_funasr=False)
    finally:
        app_module.V2Persistence = original
    identity = {"schema_version": BOUNDARY_SCHEMA, "role": "app", "port": APP_PORT,
        "pid": os.getpid(), "process_start_marker": workbench_server.process_start_marker(os.getpid()),
        "data_dir": str(data_dir), "source_fingerprint": workbench_server.runtime_source_fingerprint(),
        "instrumented_test_wrapper": True, "host_limit": HOST_LIMIT, "lane_limits": LIMITS,
        "manual_max_attempts": 1, "upstream_base_is_8791": True, "audio_device_access_authorized": False,
        "automatic_enabled": automatic_enabled, "discussion_state_enabled": True}
    if profile != DEFAULT_PROFILE:
        identity.update(profile=profile, fixture_sha256=fixture_sha256,
            boundary_tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            profile_helper_sha256=hashlib.sha256(Path(__file__).with_name("participation_natural_review_profile.py").read_bytes()).hexdigest())
    fresh_json(run_dir / "app-identity.json", identity)

    @application.get("/__acceptance/identity")
    def acceptance_identity():
        return {**identity, "guard_activations": getattr(application.state.v2_persistence, "acceptance_guard_activations", 0)}

    class NoAudioSocket:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)
    application.add_middleware(NoAudioSocket)

    # This isolated server has no audio input and no provider-edit escape hatch.
    natural_scope = None
    import asyncio
    natural_scope_lock = asyncio.Lock()

    @application.middleware("http")
    async def boundary(request, call_next):
        nonlocal natural_scope
        from starlette.responses import JSONResponse
        if profile == "natural-review-short":
            from participation_natural_review_profile import INTENTS, operation_allowed
            denied = JSONResponse({"detail": {"code": "natural_fixture_operation_disabled"}}, status_code=403)
            if not operation_allowed(request.method, request.url.path, natural_scope or ""):
                return denied
            if request.method == "POST" and request.url.path == "/v2/meetings":
                try:
                    body = await request.json()
                except (ValueError, UnicodeError):
                    return denied
                mid = body.get("meeting_id") if isinstance(body, dict) else None
                if not isinstance(mid, str) or not mid.startswith(SCOPE_PREFIX + "natural_"):
                    return denied
                async with natural_scope_lock:
                    if natural_scope is not None and natural_scope != mid:
                        return denied
                    response = await call_next(request)
                    if response.status_code < 400:
                        natural_scope = mid
                    return response
            if request.method == "POST" and request.url.path.endswith("/coach/request"):
                try:
                    body = await request.json()
                except (ValueError, UnicodeError):
                    return denied
                if not isinstance(body, dict) or body.get("intent") not in INTENTS or body.get("request") != "":
                    return denied
            if request.method == "POST" and request.url.path.startswith("/v2/traces/"):
                try:
                    body = await request.json()
                except (ValueError, UnicodeError):
                    return denied
                if not isinstance(body, dict) or body.get("meeting_id") != natural_scope:
                    return denied
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.url.path == "/providers/config" or "/recordings" in request.url.path
            or request.url.path.startswith(("/asr/", "/asr-live/"))):
            return JSONResponse({"detail": {"code": "continuous_fixture_operation_disabled"}}, status_code=403)
        return await call_next(request)
    return application


def serve_app(run_dir: Path, *, profile: str = DEFAULT_PROFILE, fixture_sha256: str | None = None) -> None:
    import uvicorn
    application = build_app(run_dir) if profile == DEFAULT_PROFILE else build_app(run_dir, profile=profile, fixture_sha256=fixture_sha256)
    print(json.dumps({"status": "starting", "port": APP_PORT, "pid": os.getpid(),
        "data_dir": str(run_dir / "runtime"), "instrumented_test_wrapper": True}), flush=True)
    uvicorn.run(application, host="127.0.0.1", port=APP_PORT, log_level="warning", access_log=False)


def listener_owners(port: int) -> set[int]:
    """Windows read-only listener ownership; no command line or secrets returned."""
    if type(port) is not int or not 1 <= port <= 65535:
        return set()
    if os.name != "nt":
        return set()  # This acceptance explicitly targets the Windows host.
    command = f"@(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        owners = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
        if type(owners) is int:
            owners = [owners]
        return {item for item in owners if type(item) is int and item > 0} if isinstance(owners, list) else set()
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()


def preflight(run_dir: Path, client: httpx.Client) -> dict:
    """Read-only checks happen before mutable repository or scope creation."""
    database = run_dir / "runtime/meeting_copilot.db"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as observer:
        count, live = observer.execute("SELECT COUNT(*),SUM(state='live') FROM meetings").fetchone()
        usage = observer.execute("SELECT COUNT(*) FROM llm_usage_ledger").fetchone()[0]
    if count or live or usage:
        raise AcceptanceStop("preexisting_runtime", "continuous acceptance requires an empty fresh runtime; existing scopes untouched")
    identity = client.get("/__acceptance/identity").json()
    health = client.get("/health").json()
    schema = client.get("/v2/diagnostics/application-schema").json()
    provider = client.get("/providers/config").json()
    with httpx.Client(trust_env=False, timeout=5) as boundary_client:
        proxy_state = boundary_client.get(f"http://127.0.0.1:{PROXY_PORT}/status").json()
    # make_server's real GET contract is flat: status/model plus snapshot keys.
    budget = proxy_state
    file_identity = json.loads((run_dir / "app-identity.json").read_text(encoding="utf-8"))
    proxy_identity = json.loads((run_dir / "proxy-identity.json").read_text(encoding="utf-8"))
    checks = {"app_identity": (identity.get("schema_version") == BOUNDARY_SCHEMA and identity.get("role") == "app"
        and type(identity.get("pid")) is int and identity["pid"] > 0 and bool(identity.get("process_start_marker"))
        and identity.get("port") == APP_PORT and identity.get("data_dir") == str((run_dir / "runtime").resolve())
        and identity.get("pid") == file_identity.get("pid") and identity.get("process_start_marker") == file_identity.get("process_start_marker")
        and identity.get("process_start_marker") == workbench_server.process_start_marker(identity.get("pid"))
        and listener_owners(APP_PORT) == {identity["pid"]}
        and identity.get("source_fingerprint") == workbench_server.runtime_source_fingerprint()
        and identity.get("instrumented_test_wrapper") is True and identity.get("host_limit") == HOST_LIMIT
        and identity.get("lane_limits") == LIMITS and identity.get("guard_activations") == 0),
        "proxy_identity": (proxy_identity.get("schema_version") == BOUNDARY_SCHEMA and proxy_identity.get("port") == PROXY_PORT
        and proxy_identity.get("role") == "proxy" and type(proxy_identity.get("pid")) is int
        and bool(proxy_identity.get("process_start_marker"))
        and proxy_identity.get("ledger") == str(run_dir / "proxy-ledger.json")
        and proxy_identity.get("process_start_marker") == workbench_server.process_start_marker(proxy_identity.get("pid"))
        and listener_owners(PROXY_PORT) == {proxy_identity["pid"]}),
        # The actual GET provider contract strips /v1 from its canonical base.
        "provider_config": (provider.get("configured") is True
        and provider.get("base_url", "").rstrip("/") == f"http://127.0.0.1:{PROXY_PORT}"
        and provider.get("model") == "gpt-6-sol" and provider.get("realtime_model") == "gpt-6-sol"
        and provider.get("api_style") == "responses"),
        "health": health.get("status") in {"ok", "ready"},
        "schema": (schema.get("schema_version") == workbench_server.APPLICATION_SCHEMA_VERSION
        and schema.get("final_version") == DB_SCHEMA_VERSION and schema.get("status") == "ready"
        and schema.get("storage") == "sqlite"),
        "fresh_budget": (budget.get("maximum_forward_attempts") == UPSTREAM_LIMIT and budget.get("reserved_forward_attempts") == 0
        and budget.get("budget_rejections") == 0 and budget.get("upstream_retries") == 0)}
    if not all(checks.values()):
        raise AcceptanceStop("preflight_boundary_mismatch", "continuous boundary identity/config/budget mismatch", checks=checks)
    return {"identity": identity, "health_ok": health.get("status") in {"ok", "ready"},
        "schema_contract": schema.get("schema_version"), "schema_version": schema.get("final_version"), "proxy_identity": proxy_identity,
        "empty_runtime": True, "proxy_budget": budget, "checks": checks}


def in_flight(trace: dict | None) -> bool:
    return bool(trace and any(type(item.get("started_at_monotonic_ns")) is int
        and item.get("completed_at_monotonic_ns") is None and item.get("outcome") is None
        for item in (trace.get("execution") or {}).get("provider_attempts") or []))


def own_failure(job: dict) -> bool:
    output = job.get("output") or {}
    status = ((output.get("applied") or {}).get("coach_decision") or {}).get("status") or (output.get("coach") or {}).get("status")
    return job.get("status") in {"failed", "cancelled"} or status in {"failed", "timed_out", "stale", "cancelled"}


def source_versions(card: dict | None) -> set[str]:
    return {str(item.get("version_id")) for item in (card or {}).get("source_evidence") or [] if item.get("version_id")}


def history_contains(snapshot: dict, card: dict) -> bool:
    keys = ("analysis_kind", "proposal_type", "topic_id", "analysis_basis_revision", "supporting_facts", "assumptions", "material_context", "source_evidence")
    return any(item.get("job_id") == card.get("job_id") and item.get("decision_id") == card.get("decision_id")
        and (item.get("say_this") or item.get("recommendation") or item.get("question")) == (card.get("say_this") or card.get("recommendation"))
        and all(item.get(key) == card.get(key) for key in keys) for item in snapshot.get("coach_history") or [])


def responded_point_owner_check(before: dict, after: dict, response_source: dict) -> dict:
    """Check an actual response on its existing owner, never treat closing as answering."""
    original = before.get("discussion") or {}
    original_id = original.get("work_item_id")
    known_ids = {point.get("id") for point in (original.get("context") or {}).get("open_points", [])
        if isinstance(point, dict) and point.get("id") and point.get("entity_type") == "open_question"}
    owner = next((item for item in after.get("all_topic_work_items", []) if item.get("work_item_id") == original_id), {})
    context = owner.get("context") or {}
    source_id = response_source.get("segment_id")
    source_hash = (response_source.get("seed") or {}).get("source_hash")
    matched = [point for point in context.get("responded_points", []) if isinstance(point, dict)
        and point.get("id") in known_ids and point.get("entity_type") == "open_question"
        and source_id and source_id in (point.get("source_refs") or []) and source_hash
        and any(evidence == {"segment_id": source_id, "revision": 1, "evidence_hash": source_hash}
            for evidence in point.get("evidence", []))]
    matched_ids = {point["id"] for point in matched}
    still_open = {point.get("id") for point in context.get("open_points", []) if isinstance(point, dict)}
    current = after.get("discussion") or {}
    current_context = current.get("context") or {}
    copied = current.get("work_item_id") != original_id and any(point.get("id") in matched_ids
        for field in ("open_points", "responded_points") for point in current_context.get(field, []) if isinstance(point, dict))
    return {"passed": bool(original_id and owner and matched_ids and not matched_ids & still_open and not copied),
        "response_owner_topic_id": original_id, "current_topic_id": current.get("work_item_id"),
        "matched_existing_question_ids": sorted(matched_ids), "response_source_id": source_id,
        "response_revision_hash_checked": bool(matched_ids), "answered_question_still_open": bool(matched_ids & still_open),
        "response_copied_into_new_topic": bool(copied)}


def close_scope(store, mid: str) -> None:
    if not mid.startswith(SCOPE_PREFIX):
        raise ValueError("refusing to close a scope outside this acceptance")
    with store._write_transaction():
        now = time.time_ns() // 1_000_000
        store._conn.execute("UPDATE meetings SET state='ended',ended_at_ms=? WHERE id=?", (now, mid))
        store._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_continuous_scope_closed',lease_owner=NULL,lease_until_ms=NULL,completed_at_ms=COALESCE(completed_at_ms,?),updated_at_ms=? WHERE meeting_id=? AND status IN ('pending','running','retry_wait')", (now, now, mid))


class ContinuousObserver:
    def __init__(self, run_dir: Path, report_path: Path, report: dict):
        self.run_dir, self.report_path, self.report = run_dir, report_path, report
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{APP_PORT}", trust_env=False, timeout=30)
        self.store = self.live = None
        self.mid = ""
        self.seen = {}
        self.public_texts = {}

    def http(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            # Failure bodies/Provider exception strings are deliberately omitted.
            raise AcceptanceStop("http_operation_failed", "continuous HTTP operation failed", http_status=response.status_code)
        return response.json()

    def emit(self, stage, **fields):
        event = {"stage": stage, "observed_at_ms": time.time_ns() // 1_000_000,
            "meeting_id": self.mid or None, **fields}
        self.report.setdefault("journal", []).append(event)
        update_json(self.report_path, self.report)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def collect(self):
        jobs = self.store.list_jobs(meeting_id=self.mid)
        counts = host_budget(jobs)
        identity = self.http("GET", "/__acceptance/identity")
        if identity.get("source_fingerprint") != workbench_server.runtime_source_fingerprint():
            raise AcceptanceStop("runtime_source_changed", "continuous runtime source changed after preflight")
        with httpx.Client(trust_env=False, timeout=5) as client:
            proxy_state = client.get(f"http://127.0.0.1:{PROXY_PORT}/status").json()
        self.report.update(host_budget=counts, proxy_budget=proxy_state, guard_activations=identity["guard_activations"])
        for job in jobs:
            signature = (job["status"], job["attempts"])
            if self.seen.get(job["id"]) != signature:
                self.seen[job["id"]] = signature
                self.emit("job_transition", job_id=job["id"], lane=lane(job), status=job["status"],
                    attempts=job["attempts"], error_class=job.get("error_class"))
        if over_budget(counts) or identity["guard_activations"] or proxy_state["budget_rejections"]:
            raise AcceptanceStop("budget_boundary_activated", "continuous acceptance budget boundary activated")
        return jobs, proxy_state

    def checkpoint(self, stage, *, job=None):
        snapshot = self.http("GET", f"/v2/meetings/{self.mid}/snapshot")
        discussion = self.store.get_current_discussion(self.mid)
        traces = self.http("GET", f"/v2/meetings/{self.mid}/traces")["traces"]
        item = {"stage": stage, "observed_at_ms": time.time_ns() // 1_000_000,
            "discussion": discussion, "snapshot": {key: snapshot.get(key) for key in
                ("coach_intervention", "coach_decision", "coach_history", "runtime", "discussion")},
            "all_topic_work_items": self.store.list_agent_work_items(self.mid, kind="discussion"),
            "traces": traces}
        if job is not None:
            item["own_outcome"] = value.own_job_outcome(job, snapshot)
            item["fact_checks"] = value.verify_facts(item["own_outcome"]["own_intervention"], self.public_texts)
            item["failure_diagnostics"] = (job.get("output") or {}).get("failure_diagnostics")
        self.report.setdefault("checkpoints", []).append(item)
        self.emit(stage, job_id=(job or {}).get("id"), workbench_url=f"http://127.0.0.1:{APP_PORT}/workbench?meeting_id={self.mid}")
        return item

    def pause_for_inspection(self):
        deadline = time.monotonic() + self.report.get("inspection_seconds", 15)
        while time.monotonic() < deadline:
            self.collect()
            time.sleep(.1)

    def wait_job(self, kind, ordinal, *, inflight=False, seconds=180):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            jobs, proxy_state = self.collect()
            matches = sorted((job for job in jobs if lane(job) == kind), key=lambda job: (job["created_at_ms"], job["id"]))
            if len(matches) >= ordinal:
                job = matches[ordinal - 1]
                if own_failure(job):
                    self.checkpoint(f"{kind}_{ordinal}_failed", job=job)
                    raise AcceptanceStop("own_job_failed", "own job or own decision failed; no paid retry")
                if inflight:
                    traces = self.http("GET", f"/v2/meetings/{self.mid}/traces")["traces"]
                    trace = next((item for item in traces if item.get("job_id") == job["id"]), None)
                    active_post = any(item["status"] == "reserved" and item["started_at_ms"] >= job["created_at_ms"] for item in proxy_state["events"])
                    if job["status"] == "running" and in_flight(trace) and active_post:
                        self.report["inflight_observation"] = {"job_id": job["id"], "trace": trace,
                            "proxy_reservations": proxy_state["reserved_forward_attempts"],
                            "observed_at_ms": time.time_ns() // 1_000_000, "actual_forward_pending": True}
                        return job
                    if job["status"] in TERMINALS:
                        self.report["preemption_coverage"] = "uncovered_overlap_missed"
                        raise AcceptanceStop("overlap_missed", "second automatic task finished before a real overlap was observed")
                elif job["status"] == "succeeded":
                    return job
            time.sleep(.1)
        raise AcceptanceStop("observation_timeout", "natural timer or job observation timed out; no direct admission fallback")

    def append(self, index):
        segment_id = f"continuous-public-{index + 1}"
        text = INPUTS[index]
        self.public_texts[segment_id] = text
        seed = value.seed_synthetic_final(self.store, self.live, self.mid, {"segment_id": segment_id, "text": text}, index + 1)
        self.report.setdefault("sources", []).append({"segment_id": segment_id, "text": text,
            "seed": seed, "appended_at_ms": time.time_ns() // 1_000_000})
        self.emit("public_input_appended", segment_id=segment_id, characters=len(text), semantic_fixture_applied=False)

    def manual(self, intent, request):
        # Topic/basis are optional: the normal host freezes the current state.
        result = self.http("POST", f"/v2/meetings/{self.mid}/coach/request",
            json={"intent": intent, "request": request}, headers={"Idempotency-Key": uuid.uuid4().hex})
        self.emit("manual_request_accepted", intent=intent, job_id=result["job"]["id"])
        return result["job"]["id"]

    def material_v1(self):
        material_set = self.http("POST", "/v2/material-sets")["material_set"]
        sid = material_set["id"]
        uploaded = self.http("POST", f"/v2/material-sets/{sid}/attachments",
            files={"file": ("public-payment-requirements.md", MATERIAL_V1.encode("utf-8"), "text/markdown")},
            data={"title": "公开付款超时需求", "purpose": "会议验收公开合成资料，不是真实公司需求"})
        aid = uploaded["attachment"]["id"]
        path = f"/v2/material-sets/{sid}/attachments/{aid}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            detail = self.http("GET", path)
            if detail.get("version") is not None:
                break
            if (detail.get("job") or {}).get("status") in {"failed", "cancelled"}:
                raise AcceptanceStop("material_conversion_failed", "deterministic material conversion failed; no retry")
            time.sleep(.1)
        else:
            raise AcceptanceStop("material_conversion_timeout", "material conversion timeout")
        material_set = self.http("GET", f"/v2/material-sets/{sid}")["material_set"]
        self.http("POST", f"/v2/meetings/{self.mid}/material-set",
            json={"material_set_id": sid, "expected_revision": material_set["revision"]})
        enabled = self.http("PUT", path + "/usage", json={"version_id": detail["version"]["id"],
            "enabled": True, "selected_span_ids": None, "expected_revision": detail["attachment"]["revision"]})
        self.report["material"] = {"set_id": sid, "attachment_id": aid, "version1_id": detail["version"]["id"],
            "v1_enabled": enabled, "v1_text": MATERIAL_V1, "v2_text": MATERIAL_V2}
        self.emit("material_v1_explicitly_enabled", material_set_id=sid, version_id=detail["version"]["id"])

    def material_v2(self, previous_card):
        material = self.report["material"]
        path = f"/v2/material-sets/{material['set_id']}/attachments/{material['attachment_id']}"
        before_set = self.http("GET", f"/v2/material-sets/{material['set_id']}")["material_set"]
        before = self.http("GET", path)
        saved = self.http("POST", path + "/versions", json={"content": MATERIAL_V2,
            "base_version_id": material["version1_id"], "expected_revision": before["attachment"]["revision"]})
        after_set = self.http("GET", f"/v2/material-sets/{material['set_id']}")["material_set"]
        unchanged = saved["attachment"].get("active_version_id") == material["version1_id"] and after_set["context_revision"] == before_set["context_revision"]
        material.update(version2_id=saved["version"]["id"], save_only_active_v1_unchanged=unchanged)
        self.checkpoint("material_v2_saved_v1_still_active")
        self.pause_for_inspection()
        if not unchanged:
            raise AcceptanceStop("save_changed_active_scope", "saving a version changed active material scope without explicit use")
        enabled = self.http("PUT", path + "/usage", json={"version_id": saved["version"]["id"],
            "enabled": True, "selected_span_ids": None, "expected_revision": saved["attachment"]["revision"]})
        material["v2_enabled"] = enabled
        checkpoint = self.checkpoint("material_v2_explicit_use_retraction")
        current = checkpoint["snapshot"].get("coach_intervention") or {}
        material["old_current_retracted"] = not previous_card or current.get("decision_id") != previous_card.get("decision_id")
        material["old_card_inside_ttl_at_usage_change"] = type(previous_card.get("valid_until_ms")) is int and checkpoint["observed_at_ms"] < previous_card["valid_until_ms"]
        material["old_typed_history_retained"] = bool(previous_card and history_contains(checkpoint["snapshot"], previous_card))
        source_checks = []
        for source in (previous_card or {}).get("source_evidence") or []:
            if source.get("source_type") != "meeting_attachment":
                continue
            source_id = source["source_id"]
            active = self.client.get(f"/v2/meetings/{self.mid}/sources/{source_id}")
            historical = self.client.get(f"/v2/meetings/{self.mid}/sources/{source_id}", params={"allow_historical": "true"})
            source_checks.append({"source_id": source_id, "version_id": source.get("version_id"),
                "current_status": active.status_code, "historical_status": historical.status_code,
                "historical_source_state": historical.json().get("source", {}).get("source_state") if historical.status_code == 200 else None})
        material["old_source_read_checks"] = source_checks

    def run(self):
        from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
        self.report["preflight"] = preflight(self.run_dir, self.client)
        self.store = value.open_synthetic_store(self.run_dir / "runtime/meeting_copilot.db")
        self.live = SqliteAsrLiveSessionRepository(self.run_dir / "runtime")
        self.mid = SCOPE_PREFIX + uuid.uuid4().hex[:12]
        self.report["meeting_id"] = self.mid
        self.http("POST", "/v2/meetings", json={"meeting_id": self.mid,
            "title": "连续参与验收 · 公开合成文本 · 无音频设备"}, headers={"Idempotency-Key": self.mid})
        preparation = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
        preparation = self.http("PUT", f"/v2/meetings/{self.mid}/preparation",
            json={**preparation, "live_analysis_profile": "participation", "proactive_suggestion_policy": "low_frequency"})
        self.report["preparation"] = preparation
        value.create_synthetic_live_record(self.live, self.mid)
        self.emit("scope_created", workbench_url=f"http://127.0.0.1:{APP_PORT}/workbench?meeting_id={self.mid}",
            inspection_command=f"GET http://127.0.0.1:{APP_PORT}/v2/meetings/{self.mid}/snapshot")
        try:
            self.append(0)
            self.wait_job("discussion_state", 1)
            self.checkpoint("state1_natural_success")
            auto1 = self.wait_job("automatic", 1)
            first = self.checkpoint("automatic1_current_inside_ttl", job=auto1)
            if not first["own_outcome"]["valid_intervention"] or not first["own_outcome"]["current_card_for_request"]:
                raise AcceptanceStop("automatic_publication_invalid", "automatic1 did not publish its own valid current card")
            self.pause_for_inspection()
            self.material_v1()
            self.append(1)
            self.wait_job("discussion_state", 2)
            second = self.checkpoint("state2_responded_point_natural_success")
            auto2 = self.wait_job("automatic", 2, inflight=True)
            manual_id = self.manual("formulate_response", "刚才通知失败不能重复发权益已经回应，请针对当前付款超时和查询暂不可用的处理提出一句可在会上说的建议，不重复已经讨论过的问题。")
            self.report["overlap_manual_job_id"] = manual_id
            self.report["overlap_automatic_job_id"] = auto2["id"]
            manual1 = self.wait_job("manual", 1)
            overlap = self.checkpoint("manual1_after_actual_automatic_preemption", job=manual1)
            automatic_after = self.store.get_job(auto2["id"])
            current = overlap["snapshot"].get("coach_intervention") or {}
            self.report["preemption_coverage"] = {"observed_provider_inflight": True,
                "automatic_terminal_status": automatic_after["status"], "automatic_error_class": automatic_after.get("error_class"),
                "manual_preempted_ids": (manual1.get("request_context") or {}).get("preempted_job_ids"),
                "automatic_cannot_be_current_after_manual": current.get("job_id") != auto2["id"]}
            if not overlap["own_outcome"]["valid_intervention"] or not overlap["own_outcome"]["current_card_for_request"]:
                raise AcceptanceStop("manual_publication_invalid", "manual response failed after overlap; old current card cannot count as success")
            previous_card = overlap["snapshot"]["coach_intervention"]
            # Change the cost policy only after this request's own success, so
            # preparation changes cannot be confused with in-flight preemption.
            preparation = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
            self.http("PUT", f"/v2/meetings/{self.mid}/preparation", json={**preparation, "proactive_suggestion_policy": "off"})
            off_checkpoint = self.checkpoint("policy_off_after_manual_success", job=manual1)
            self.report["policy_off_preserved_manual_card"] = (off_checkpoint["snapshot"].get("coach_intervention") or {}).get("decision_id") == previous_card["decision_id"]
            self.emit("cost_control_policy_off", only_after_real_overlap=True, only_after_manual_success=True,
                global_automatic_environment_unchanged=True)
            if not self.report["policy_off_preserved_manual_card"]:
                raise AcceptanceStop("policy_change_withdrew_card", "preparation policy change withdrew the manual card; preserve this lifecycle failure")
            self.pause_for_inspection()
            self.material_v2(previous_card)
            self.wait_job("discussion_state", 3)
            self.checkpoint("state3_material_v2_rechecked_naturally")
            self.manual("compare_options", "结合本场明确启用的最新版付款超时资料，比较查询待确认与重新付款的取舍，指出新约束如何改变原候选。")
            manual2 = self.wait_job("manual", 2)
            revised = self.checkpoint("manual2_current_material_v2", job=manual2)
            revised_card = revised["own_outcome"]["own_intervention"] or {}
            version2 = self.report["material"]["version2_id"]
            self.report["material"]["new_card_uses_v2"] = version2 in source_versions(revised_card)
            v2_refs = {source["source_id"] for source in revised_card.get("source_evidence") or [] if source.get("version_id") == version2}
            self.report["material"]["visible_fact_unique_to_v2"] = any(fact.get("source_ref") in v2_refs and fact.get("text")
                and fact["text"] in MATERIAL_V2 and fact["text"] not in MATERIAL_V1 and not fact["text"].lstrip().startswith("#")
                for fact in revised_card.get("supporting_facts") or [])
            self.pause_for_inspection()
            self.append(2)
            self.wait_job("discussion_state", 4)
            final_state = self.checkpoint("state4_new_topic_naturally_understood")
            self.manual("freeform", "只针对现在的欢迎页讨论判断是否有值得我补充的一句话；如果当前没有价值请保持沉默，不重新提出前一个付款议题的问题。")
            manual3 = self.wait_job("manual", 3)
            final = self.checkpoint("manual3_new_topic_or_validated_silence", job=manual3)
            old_discussion, new_discussion = second.get("discussion") or {}, final_state.get("discussion") or {}
            new_context = new_discussion.get("context") or {}
            old_item = next((item for item in final_state["all_topic_work_items"] if item["work_item_id"] == old_discussion.get("work_item_id")), {})
            self.report["responded_point_owner_evidence"] = responded_point_owner_check(first, second, self.report["sources"][1])
            self.report["continuity_checks"] = {"new_topic_id": bool(old_discussion.get("work_item_id") and new_discussion.get("work_item_id")
                    and old_discussion["work_item_id"] != new_discussion["work_item_id"]),
                "responded_point_has_current_input_evidence": self.report["responded_point_owner_evidence"]["passed"],
                "old_topic_closed_in_history": old_item.get("state") == "superseded" and old_item.get("context", {}).get("phase") == "closed",
                "new_open_points_use_only_new_topic_sources": all(item.get("source_refs") and set(item["source_refs"]) <= {"continuous-public-3"}
                    for item in new_context.get("open_points", []) if isinstance(item, dict)),
                "old_card_not_current": (final["snapshot"].get("coach_intervention") or {}).get("decision_id") != previous_card.get("decision_id"),
                "final_own_result_valid": final["own_outcome"]["valid_intervention"] or final["own_outcome"]["validated_silence"],
                "old_history_retained": history_contains(final["snapshot"], previous_card)}
            checks = {**self.report["continuity_checks"],
                "policy_off_preserved_manual_card": self.report["policy_off_preserved_manual_card"],
                "scope_change_retracted_old_current": self.report["material"]["old_current_retracted"],
                "usage_change_before_old_ttl": self.report["material"]["old_card_inside_ttl_at_usage_change"],
                "scope_change_retained_typed_history": self.report["material"]["old_typed_history_retained"],
                "save_only_did_not_enable_v2": self.report["material"]["save_only_active_v1_unchanged"],
                "new_advice_cited_v2": self.report["material"]["new_card_uses_v2"],
                "visible_fact_reflects_v2_change": self.report["material"]["visible_fact_unique_to_v2"],
                "observed_preempted_job_identity": auto2["id"] in ((manual1.get("request_context") or {}).get("preempted_job_ids") or []),
                "preempted_automatic_cancelled": automatic_after["status"] == "cancelled",
                "old_source_current_rejected_history_readable": bool(self.report["material"]["old_source_read_checks"])
                    and all(item["current_status"] == 409 and item["historical_status"] == 200 and item["historical_source_state"] == "stale"
                        for item in self.report["material"]["old_source_read_checks"])}
            self.report["contract_checks"] = checks
            self.report["contract_failures"] = [key for key, passed in checks.items() if not passed]
            self.report["automated_contract_passed"] = not self.report["contract_failures"]
            self.report["stop_reason"] = "all_stages_observed_manual_value_review_pending"
            self.pause_for_inspection()
        finally:
            self.finish()

    def finish(self):
        if self.store is None or not self.mid:
            return
        jobs = self.store.list_jobs(meeting_id=self.mid)
        self.report["all_jobs"] = [{key: job.get(key) for key in
            ("id", "kind", "trigger_type", "status", "attempts", "max_attempts", "created_at_ms", "completed_at_ms", "error_class", "input_transcript_seq", "input_version")}
            | {"lane": lane(job), "request_context": {key: (job.get("request_context") or {}).get(key) for key in
                ("profile", "task_mode", "topic_id", "analysis_basis_revision", "preempted_job_ids", "input_context")},
                "own_outcome": value.own_job_outcome(job, {}) if lane(job) in {"automatic", "manual"} else None,
                "state_interpretation": (job.get("output") or {}).get("state_interpretation"),
                "failure_diagnostics": (job.get("output") or {}).get("failure_diagnostics")} for job in jobs]
        with sqlite3.connect("file:" + str(self.run_dir / "runtime/meeting_copilot.db") + "?mode=ro", uri=True) as observer:
            observer.row_factory = sqlite3.Row
            self.report["usage_ledger"] = [dict(row) for row in observer.execute(
                "SELECT id,purpose,model,prompt_tokens,completion_tokens,total_tokens,timestamp_ms FROM llm_usage_ledger WHERE session_id=? ORDER BY timestamp_ms", (self.mid,))]
        events = self.store.list_events(self.mid, limit=1000)
        self.report["event_identities"] = [{"seq": event["seq"], "type": event["type"], "occurred_at_ms": event["occurred_at_ms"],
            "job_id": event["payload"].get("job_id"), "decision_id": (event["payload"].get("coach_decision") or {}).get("decision_id"),
            "decision_status": (event["payload"].get("coach_decision") or {}).get("status"),
            "lifecycle_action": (event["payload"].get("coach_decision") or {}).get("lifecycle_action")}
            for event in events]
        if self.report.get("contract_checks") and self.report.get("overlap_manual_job_id"):
            manual = self.store.get_job(self.report["overlap_manual_job_id"])
            late = [event["seq"] for event in events if event["type"] == "meeting.intelligence.applied"
                and (event["payload"].get("job_id") or (event["payload"].get("coach_decision") or {}).get("job_id")) == self.report["overlap_automatic_job_id"]
                and event["occurred_at_ms"] >= manual["created_at_ms"]
                and (event["payload"].get("coach_decision") or {}).get("status") == "intervention"]
            self.report["preemption_coverage"]["late_publication_event_seqs"] = late
            self.report["contract_checks"]["preempted_automatic_no_late_publication"] = not late
            self.report["contract_failures"] = [key for key, passed in self.report["contract_checks"].items() if not passed]
            self.report["automated_contract_passed"] = not self.report["contract_failures"]
        self.report["host_budget"] = host_budget(jobs)
        self.report["upstream_accounting"] = json.loads((self.run_dir / "proxy-ledger.json").read_text(encoding="utf-8"))
        self.report["cancelled_or_output_missing_usage"] = [{"job_id": job["id"], "usage": "unknown_not_zero",
            "reason": "cancelled/incomplete host output cannot establish unbilled upstream"}
            for job in jobs if lane(job) and int(job["attempts"] or 0) > 0 and (job["status"] == "cancelled" or not job.get("output"))]
        close_scope(self.store, self.mid)
        self.report["scope_closed_without_paid_postprocessing"] = True
        self.emit("scope_closed", no_business_generation_or_capture=True)


def execute(run_dir: Path, report_path: Path, report: dict, *, observer_type=None) -> int:
    observer = (observer_type or ContinuousObserver)(run_dir, report_path, report)
    try:
        observer.run()
    except Exception as exc:
        report["error_class"] = type(exc).__name__
        report["stop_reason"] = "failure_preserved_without_paid_retry"
        report["stop_diagnostic"] = {"code": exc.code if isinstance(exc, AcceptanceStop)
            else "transport_failure" if isinstance(exc, httpx.TransportError) else "unclassified"}
        if isinstance(exc, ReportFileSystemError):
            report["stop_diagnostic"] = dict(exc.diagnostic)
        if isinstance(exc, AcceptanceStop):
            report["stop_diagnostic"].update(checks=exc.checks, http_status=exc.http_status)
        # Exception messages and failed model raw output are intentionally omitted.
        report_failure = exc if isinstance(exc, ReportFileSystemError) else None
        if observer.store is not None and observer.mid and not report.get("scope_closed_without_paid_postprocessing"):
            try:
                observer.finish()
            except ReportFileSystemError as finish_exc:
                report_failure = report_failure or finish_exc
        if report_failure is not None:
            _record_report_failure(run_dir, report_failure, report)
            return 1
        try:
            update_json(report_path, report)
        except ReportFileSystemError as write_exc:
            _record_report_failure(run_dir, write_exc, report)
        return 1
    finally:
        if observer.live is not None:
            observer.live.close()
        if observer.store is not None:
            observer.store.close()
        observer.client.close()
    try:
        update_json(report_path, report)
    except ReportFileSystemError as write_exc:
        _record_report_failure(run_dir, write_exc, report)
        return 1
    return 0 if report.get("automated_contract_passed") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--serve-app", action="store_true")
    modes.add_argument("--serve-proxy", action="store_true")
    parser.add_argument("--profile", choices=(DEFAULT_PROFILE, "natural-review-short"), default=DEFAULT_PROFILE)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--pdf-receipt", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--inspection-seconds", type=int, choices=range(0, 31), default=15)
    args = parser.parse_args(argv)
    natural = args.profile == "natural-review-short"
    profile_module = fixture = None
    if natural:
        import participation_natural_review_profile as profile_module
        fixture = profile_module.read_fixture(args.fixture or profile_module.DEFAULT_FIXTURE)
    elif args.fixture or args.pdf_receipt:
        parser.error("fixture/PDF receipt options require the natural-review-short profile")
    run_dir = (args.run_dir or (profile_module.DEFAULT_RUN_DIR if natural else DEFAULT_RUN_DIR)).resolve()
    if args.serve_proxy:
        serve_proxy(run_dir)
        return 0
    if args.serve_app:
        if natural:
            serve_app(run_dir, profile=args.profile, fixture_sha256=fixture["sha256"])
        else:
            serve_app(run_dir)
        return 0
    report_path = (args.report or run_dir / ("report.json" if args.execute else "plan.json")).resolve()
    report = profile_module.plan(run_dir, fixture, args.pdf_receipt) if natural else plan(run_dir)
    report["inspection_seconds"] = args.inspection_seconds
    report["executed"] = bool(args.execute)
    report["tool_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fresh_json(report_path, report)
    if args.execute:
        if not natural:
            return execute(run_dir, report_path, report)
        try:
            observer = profile_module.observer_type(sys.modules[__name__], fixture, args.pdf_receipt)
        except (OSError, ValueError) as exc:
            report.update(error_class=type(exc).__name__, stop_reason="input_asset_preflight_failed_before_network",
                stop_diagnostic={"code": "input_asset_preflight_failed"}, provider_calls_made=0)
            update_json(report_path, report)
            return 1
        return execute(run_dir, report_path, report, observer_type=observer)
    print(json.dumps({"executed": False, "plan": str(report_path), "budget": report["budget"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
