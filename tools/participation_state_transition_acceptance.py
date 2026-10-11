"""Plan by default; two natural state jobs on a fresh, device-free 8771/8791 boundary.

This narrow follow-up never resumes the continuous R02 runtime or its remaining
budget. Its private support-module namespace leaves the original 9/14 profile
unchanged. No semantic event, direct admission, automatic card or manual request
is manufactured. Only the normal timer interprets the two public input segments.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path


_SUPPORT_PATH = Path(__file__).with_name("participation_continuous_acceptance.py")
_SPEC = importlib.util.spec_from_file_location("_state_transition_continuous_support", _SUPPORT_PATH)
support = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = support
_SPEC.loader.exec_module(support)

PROFILE = "state_transition_only.v1"
DEFAULT_RUN_DIR = support.ROOT / "artifacts/realtime-participation-20261007/state-transition-r01"
support.LIMITS = {"discussion_state": 2, "automatic": 0, "manual": 0}
support.HOST_LIMIT = support.UPSTREAM_LIMIT = 2
support.SCOPE_PREFIX = "participation_state_transition_"
support.BOUNDARY_SCHEMA = "participation_state_transition_boundary.v1"
support.STOP_CODES |= {"expected_old_questions_missing"}


class StateAttemptBudget(support.proxy.AttemptBudget):
    def __post_init__(self):
        if type(self.limit) is not int or self.limit != 2:
            raise ValueError("state-transition proxy requires exactly two attempts")


support.ContinuousAttemptBudget = StateAttemptBudget
_regular_preflight = support.preflight


def preflight(run_dir, client):
    result = _regular_preflight(run_dir, client)
    identity = result["identity"]
    if identity.get("automatic_enabled") is not False or identity.get("discussion_state_enabled") is not True:
        raise support.AcceptanceStop("preflight_boundary_mismatch", "state-only profile mismatch",
            checks={"app_identity": False})
    return result


support.preflight = preflight

NATURAL_STATE_PROFILE = "natural-state-diagnostic"
NATURAL_STATE_MEETING_TITLE = "自然状态诊断 · 公开B1和B2 · 两次调用 · 无音频设备"
NATURAL_STATE_RUN_DIR = support.ROOT / "artifacts/realtime-participation-20261007/natural-state-diagnostic-r01"
_NATURAL_SPEC = importlib.util.spec_from_file_location("_natural_state_diagnostic_support", _SUPPORT_PATH)
natural_support = importlib.util.module_from_spec(_NATURAL_SPEC)
sys.modules[_NATURAL_SPEC.name] = natural_support
_NATURAL_SPEC.loader.exec_module(natural_support)
natural_support.LIMITS = {"discussion_state": 2, "automatic": 0, "manual": 0}
natural_support.HOST_LIMIT = natural_support.UPSTREAM_LIMIT = 2
natural_support.SCOPE_PREFIX = "participation_natural_state_diagnostic_"
natural_support.BOUNDARY_SCHEMA = "participation_natural_state_diagnostic_boundary.v1"
natural_support.ContinuousAttemptBudget = StateAttemptBudget
_natural_regular_preflight = natural_support.preflight


def natural_state_preflight(run_dir, client):
    import participation_natural_review_profile as natural
    result = _natural_regular_preflight(run_dir, client)
    identity = result["identity"]
    if (identity.get("automatic_enabled") is not False or identity.get("discussion_state_enabled") is not True
            or identity.get("profile") != NATURAL_STATE_PROFILE
            or identity.get("fixture_sha256") != natural.FIXTURE_SHA
            or identity.get("profile_helper_sha256") != natural.sha(Path(natural.__file__).read_bytes())
            or identity.get("boundary_tool_sha256") != natural.sha(_SUPPORT_PATH.read_bytes())
            or identity.get("diagnostic_tool_sha256") != natural.sha(Path(__file__).read_bytes())
            or identity.get("pi_bridge_prewarm_enabled") is not False
            or identity.get("realtime_coach_enabled") is not True):
        raise natural_support.AcceptanceStop("profile_boundary_mismatch", "natural state-only profile mismatch")
    return result


natural_support.preflight = natural_state_preflight


def natural_state_plan(run_dir: Path, fixture: dict, pdf_receipt_path: Path) -> dict:
    import participation_natural_review_profile as natural
    record = natural.plan(run_dir, fixture, pdf_receipt_path)
    record.update(schema_version="participation_natural_state_diagnostic.v1", profile=NATURAL_STATE_PROFILE,
        budget={"host_jobs": 2, "state": 2, "automatic": 0, "manual": 0,
            "forwarded_upstream_posts": 2, "attempts_per_host_job": 1, "retries": 0},
        runtime_flags={"discussion_automatic_enabled": False, "discussion_state_enabled": True,
            "realtime_coach_enabled": True, "pi_bridge_prewarm_enabled": False,
            "preparation_policy": "off", "refiner_policy": "on_demand"},
        stages=[{"block_id": step["block_id"], "planned_offset_seconds": step["planned_release_offset_seconds"],
            "wait": "normal natural state timer", "material_scope": step["material_scope"]}
            for step in fixture["data"]["adapter_plan"]["steps"][:2]],
        input="Only the six frozen public B1/B2 turn.text sources; three real v1 uploads, only API/PRODUCT v1 enabled",
        ui_signal_contract=None, inspection_seconds=0, no_ui_or_dom_acceptance_claim=True,
        first_turn_gate="not_applicable_state_only", preemption="not_tested_state_only",
        evaluation_limits="Two natural state diagnostic calls only; not Pi contribution value, ASR or latency percentiles.")
    return record


def natural_state_operation_allowed(method: str, path: str, mid: str, set_id: str,
                                    attachment_ids=()) -> bool:
    if any(word in path.lower() for word in ("asr", "audio", "device", "recording", "microphone", "capture", "playback")):
        return False
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if method == "POST" and path == "/v2/material-sets":
        return not set_id and not mid
    if method == "POST" and path == "/v2/meetings":
        return bool(set_id) and not mid
    if method == "POST" and set_id and path == f"/v2/material-sets/{set_id}/attachments":
        return not mid
    if method == "PUT" and mid and path == f"/v2/meetings/{mid}/preparation":
        return True
    return bool(method == "PUT" and mid and set_id and path in {
        f"/v2/material-sets/{set_id}/attachments/{aid}/usage" for aid in attachment_ids})


def build_natural_state_app(run_dir: Path, fixture: dict, pdf_receipt_path: Path):
    import asyncio
    import participation_natural_review_profile as natural
    from starlette.responses import JSONResponse, Response
    pdf = natural.read_pdf_receipt(pdf_receipt_path, fixture)
    application = natural_support.build_app(run_dir, automatic_enabled=False,
        profile=NATURAL_STATE_PROFILE, fixture_sha256=fixture["sha256"])
    # support.build_app explicitly sets this to 1. Override after construction,
    # before the application's lifespan evaluates the existing prewarm switch.
    os.environ["MEETING_COPILOT_PI_BRIDGE_PREWARM"] = "0"
    expected_files = {source["filename"]: {"material_id": source["material_id"],
        "sha256": natural.sha(pdf["bytes"] if source["material_id"] == "M_PRODUCT"
            else source["versions"][0]["content"].encode())} for source in fixture["data"]["materials"]}
    owned = {"meeting_id": "", "set_id": "", "attachments": {}, "enabled": set()}
    lock = asyncio.Lock()
    policy = "camera=(), microphone=(), display-capture=(), speaker-selection=()"

    async def response_json(response, updates=None):
        body = b"".join([chunk async for chunk in response.body_iterator])
        data = json.loads(body)
        if updates:
            data.update(updates)
            body = json.dumps(data, ensure_ascii=False).encode()
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return data, Response(body, status_code=response.status_code, headers=headers,
            media_type=response.media_type, background=response.background)

    @application.middleware("http")
    async def natural_state_boundary(request, call_next):
        denied = JSONResponse({"detail": {"code": "natural_state_operation_disabled"}}, status_code=403,
            headers={"Permissions-Policy": policy})
        path, method = request.url.path, request.method
        if not natural_state_operation_allowed(method, path, owned["meeting_id"], owned["set_id"],
                [aid for aid, item in owned["attachments"].items() if item["material_id"] in {"M_API", "M_PRODUCT"}]):
            return denied
        if method in {"GET", "HEAD", "OPTIONS"}:
            response = await call_next(request)
            if method == "GET" and path == "/__acceptance/identity":
                from meeting_copilot_web_mvp.pi_coach_runtime import pi_bridge_prewarm_enabled
                _, response = await response_json(response, {
                    "diagnostic_tool_sha256": natural.sha(Path(__file__).read_bytes()),
                    "pi_bridge_prewarm_enabled": pi_bridge_prewarm_enabled(),
                    "realtime_coach_enabled": os.environ.get("MEETING_COPILOT_REALTIME_COACH_ENABLED") == "1"})
            response.headers["Permissions-Policy"] = policy
            return response
        async with lock:
            # Recheck inside the lock so simultaneous fresh-create/upload calls
            # cannot claim a second owner or duplicate the public v1 material.
            allowed_ids = [aid for aid, item in owned["attachments"].items()
                if item["material_id"] in {"M_API", "M_PRODUCT"}]
            if not natural_state_operation_allowed(method, path, owned["meeting_id"], owned["set_id"], allowed_ids):
                return denied
            upload_source = None
            try:
                if path == "/v2/meetings":
                    body = await request.json()
                    if (not isinstance(body, dict) or not isinstance(body.get("meeting_id"), str)
                            or not re.fullmatch(re.escape(natural_support.SCOPE_PREFIX) + r"[a-f0-9]{12}", body["meeting_id"])
                            or body.get("material_set_id") != owned["set_id"]
                            or type(body.get("expected_material_set_revision")) is not int
                            or request.headers.get("Idempotency-Key") != body["meeting_id"]):
                        return denied
                elif path.endswith("/attachments"):
                    await request.body()  # Replay the exact multipart bytes to the normal upload API.
                    form = await request.form()
                    upload = form.get("file")
                    upload_source = expected_files.get(getattr(upload, "filename", None))
                    uploaded_hash = natural.sha(await upload.read()) if upload_source is not None else None
                    if upload is not None:
                        await upload.close()
                    if (upload_source is None or upload_source["material_id"] in {
                            item["material_id"] for item in owned["attachments"].values()}
                            or uploaded_hash != upload_source["sha256"]):
                        return denied
                elif path.endswith("/preparation"):
                    body = await request.json()
                    if (not isinstance(body, dict) or body.get("live_analysis_profile") != "participation"
                            or body.get("proactive_suggestion_policy") != "off"):
                        return denied
                elif path.endswith("/usage"):
                    body = await request.json()
                    aid = path.split("/")[-2]
                    repo = application.state.meeting_materials_repository
                    attachment = repo.get_attachment(owned["set_id"], aid)
                    version = repo.get_version(owned["set_id"], aid, str(body.get("version_id") or ""))
                    if (set(body) != {"version_id", "enabled", "selected_span_ids", "expected_revision"}
                            or body["enabled"] is not True or body["selected_span_ids"] is not None
                            or type(body["expected_revision"]) is not int or version["number"] != 1
                            or version["id"] != attachment["latest_version_id"] or aid in owned["enabled"]):
                        return denied
            except (KeyError, ValueError, TypeError, AttributeError):
                return denied
            response = await call_next(request)
            if response.status_code < 400 and path != f"/v2/meetings/{owned['meeting_id']}/preparation":
                data, response = await response_json(response)
                if path == "/v2/material-sets":
                    owned["set_id"] = data["material_set"]["id"]
                elif path == "/v2/meetings":
                    owned["meeting_id"] = body["meeting_id"]
                elif upload_source:
                    owned["attachments"][data["attachment"]["id"]] = upload_source
                elif path.endswith("/usage"):
                    owned["enabled"].add(aid)
            response.headers["Permissions-Policy"] = policy
            return response
    return application


def natural_state_observer_type(fixture: dict, pdf_receipt_path: Path):
    import participation_natural_review_profile as natural
    base = natural.observer_type(natural_support, fixture, pdf_receipt_path)

    class NaturalStateDiagnosticObserver(base):
        def http(self, method, path, **kwargs):
            allowed_ids = [item["attachment_id"] for key, item in self.materials.items()
                if key in {"M_API", "M_PRODUCT"}]
            if not natural_state_operation_allowed(method, path, self.mid,
                    self.report.get("material_set_id", ""), allowed_ids):
                raise natural_support.AcceptanceStop("unexpected_lane", "operation outside natural state-only profile")
            return super().http(method, path, **kwargs)

        def run(self):
            from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
            self.report["preflight"] = natural_support.preflight(self.run_dir, self.client)
            self.store = natural_support.value.open_synthetic_store(self.run_dir / "runtime/meeting_copilot.db")
            self.live = SqliteAsrLiveSessionRepository(self.run_dir / "runtime")
            try:
                self.upload_materials()
                mid = natural_support.SCOPE_PREFIX + uuid.uuid4().hex[:12]
                material_set = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")["material_set"]
                self.http("POST", "/v2/meetings", json={"meeting_id": mid,
                    "title": NATURAL_STATE_MEETING_TITLE,
                    "material_set_id": material_set["id"], "expected_material_set_revision": material_set["revision"]},
                    headers={"Idempotency-Key": mid})
                self.mid = mid
                self.report["meeting_id"] = mid
                prep = self.http("GET", f"/v2/meetings/{mid}/preparation")
                self.report["preparation"] = self.http("PUT", f"/v2/meetings/{mid}/preparation",
                    json={**prep, "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"})
                natural_support.value.create_synthetic_live_record(self.live, mid)
                self.emit("scope_created", profile=NATURAL_STATE_PROFILE,
                    workbench_url=f"http://127.0.0.1:{natural_support.APP_PORT}/workbench?meeting_id={mid}")
                for ordinal, (block, step) in enumerate(zip(fixture["data"]["blocks"][:2],
                        fixture["data"]["adapter_plan"]["steps"][:2], strict=True), 1):
                    if self.input_start_monotonic is not None:
                        timing = natural.wait_release(step["planned_release_offset_seconds"],
                            origin=self.input_start_monotonic, tick=self.collect)
                        self.report.setdefault("release_timing", []).append({"block_id": block["block_id"], **timing})
                    self.append_block(block)
                    if ordinal == 2:
                        self.enable("M_API", 1)
                        self.enable("M_PRODUCT", 1)
                        actual = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")
                        self.report["actual_enabled_scope"] = {"material_set": actual["material_set"],
                            "attachments": actual["attachments"], "acknowledged_at_ms": time.time_ns() // 1_000_000}
                    job = self.wait_job("discussion_state", ordinal)
                    self.checkpoint(f"state{ordinal}_natural_success", job=job)
                jobs, budget = self.collect()
                counts = natural_support.host_budget(jobs)
                actual = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")
                enabled = {item["id"]: item["active_version_id"] for item in actual["attachments"] if item["enabled"]}
                canonical = self.store.get_snapshot(mid)["segments"]
                expected_seq = max(item["transcript_seq"] for item in canonical)
                discussion = self.store.get_current_discussion(mid) or {}
                checks = {"two_state_host_jobs": counts["discussion_state"] == counts["host_jobs"] == 2,
                    "no_automatic_or_manual": counts["automatic"] == counts["manual"] == 0,
                    "two_actual_upstream_calls": budget["reserved_forward_attempts"] == 2,
                    "no_retry_or_guard": not counts["retried_jobs"] and not self.report["guard_activations"]
                        and not budget["budget_rejections"] and not budget["upstream_retries"],
                    "six_original_public_sources": len(canonical) == len(self.report["sources"]) == 6
                        and len({item["segment_id"] for item in self.report["sources"]}) == 6,
                    "only_two_acknowledged_v1_materials_enabled": enabled == {
                        self.materials[key]["attachment_id"]: self.materials[key]["version_ids"][1]
                        for key in ("M_API", "M_PRODUCT")},
                    "latest_canonical_input_understood": discussion.get("context", {}).get("last_semantic_seq") == expected_seq,
                    "no_coach_publication": not self.http("GET", f"/v2/meetings/{mid}/snapshot").get("coach_intervention")}
                self.report.update(contract_checks=checks, contract_failures=[key for key, value in checks.items() if not value],
                    automated_contract_passed=all(checks.values()), observed_total_elapsed_ms=round((time.monotonic()-self.start_monotonic)*1000),
                    stop_reason="two_natural_state_diagnostic_results_observed" if all(checks.values()) else "natural_state_diagnostic_contract_failed")
            finally:
                self.finish()
    return NaturalStateDiagnosticObserver


def plan(run_dir: Path) -> dict:
    return {"schema_version": "participation_state_transition_acceptance.v1", "profile": PROFILE,
        "executed": False, "run_dir": str(run_dir), "runtime": str(run_dir / "runtime"),
        "app_url": f"http://127.0.0.1:{support.APP_PORT}", "proxy_url": f"http://127.0.0.1:{support.PROXY_PORT}",
        "budget": {"host_jobs": 2, "state": 2, "automatic": 0, "manual": 0,
            "forwarded_upstream_posts": 2, "attempts_per_host_job": 1, "retries": 0},
        "runtime_flags": {"discussion_automatic_enabled": False, "discussion_state_enabled": True,
            "preparation_policy": "off", "refiner_policy": "on_demand"},
        "instrumented_test_wrapper": {"atomic_admission_ceiling": True, "activation_is_failure": True},
        "timer_gates_overridden": False, "direct_state_or_automatic_admission": False,
        "semantic_fixture_applied": False, "clock_advanced": False, "ttl_shortened": False,
        "audio_capture": False, "audio_asr": False, "websocket_input_disabled": True,
        "production_ports_untouched": [8767, 8768], "old_proxy_ledger_untouched": True,
        "application_schema_version": support.DB_SCHEMA_VERSION,
        "input": "Same first two public synthetic texts as continuous R02; canonical finals and disclosed mock live mirror only",
        "stages": [{"id": "natural_old_questions", "characters": len(support.INPUTS[0]),
            "minimum_known_open_questions": 2, "wait": "normal global state timer"},
            {"id": "natural_response_then_topic_switch", "characters": len(support.INPUTS[1]),
             "wait": "unchanged 60-second later-state cooldown, one attempt"}],
        "evaluation_limits": "Two state calls only; not a repeat of continuous/Pi/ASR acceptance or a latency percentile.",
        "inspection_seconds": 0, "proxy_credentials": "environment only; never in report or arguments"}


def build_app(run_dir: Path):
    application = support.build_app(run_dir, automatic_enabled=False)

    @application.middleware("http")
    async def state_only_boundary(request, call_next):
        from starlette.responses import JSONResponse
        allowed = (request.method in {"GET", "HEAD", "OPTIONS"}
            or request.method == "POST" and request.url.path == "/v2/meetings"
            or request.method == "PUT" and request.url.path.startswith("/v2/meetings/" + support.SCOPE_PREFIX)
            and request.url.path.endswith("/preparation"))
        if not allowed:
            return JSONResponse({"detail": {"code": "state_transition_operation_disabled"}}, status_code=403)
        return await call_next(request)
    return application


def serve_app(run_dir, *, fixture=None, pdf_receipt_path=None):
    import os
    import uvicorn
    application = (build_app(run_dir) if fixture is None
        else build_natural_state_app(run_dir, fixture, pdf_receipt_path))
    print(json.dumps({"status": "starting", "profile": PROFILE if fixture is None else NATURAL_STATE_PROFILE, "port": support.APP_PORT,
        "pid": os.getpid(), "data_dir": str(run_dir / "runtime"), "host_limit": 2}), flush=True)
    uvicorn.run(application, host="127.0.0.1", port=support.APP_PORT, log_level="warning", access_log=False)


def transition_checks(first, second, response_source) -> tuple[dict, dict]:
    owner_check = support.responded_point_owner_check(first, second, response_source)
    original = first.get("discussion") or {}
    original_id = original.get("work_item_id")
    original_ids = {point.get("id") for point in (original.get("context") or {}).get("open_points", [])
        if isinstance(point, dict) and point.get("entity_type") == "open_question" and point.get("id")}
    owner = next((item for item in second.get("all_topic_work_items", []) if item.get("work_item_id") == original_id), {})
    answered = set(owner_check["matched_existing_question_ids"])
    remaining = {point.get("id") for point in (owner.get("context") or {}).get("open_points", []) if isinstance(point, dict)}
    current = second.get("discussion") or {}
    context = current.get("context") or {}
    current_points = [point for field in ("constraints", "open_points", "responded_points")
        for point in context.get(field, []) if isinstance(point, dict)]
    checks = {"known_old_question_responded_with_current_revision_hash": owner_check["passed"],
        "old_unanswered_question_retained": bool(original_ids - answered) and original_ids - answered <= remaining,
        "old_topic_closed_in_history": owner.get("state") == "superseded" and (owner.get("context") or {}).get("phase") == "closed",
        "new_topic_independent": bool(original_id and current.get("work_item_id") != original_id),
        "new_topic_did_not_inherit_old_questions": all(point.get("id") not in original_ids for point in current_points),
        "new_topic_has_grounded_open_question": bool(context.get("open_points"))
            and all(point.get("source_refs") and set(point["source_refs"]) <= {response_source["segment_id"]} for point in current_points)}
    return checks, owner_check


class StateTransitionObserver(support.ContinuousObserver):
    def run(self):
        from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
        self.report["preflight"] = support.preflight(self.run_dir, self.client)
        self.store = support.value.open_synthetic_store(self.run_dir / "runtime/meeting_copilot.db")
        self.live = SqliteAsrLiveSessionRepository(self.run_dir / "runtime")
        self.mid = support.SCOPE_PREFIX + uuid.uuid4().hex[:12]
        self.report["meeting_id"] = self.mid
        self.http("POST", "/v2/meetings", json={"meeting_id": self.mid, "title": "旧题回应与切题验收 · 两次状态调用 · 无音频"},
            headers={"Idempotency-Key": self.mid})
        preparation = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
        self.report["preparation"] = self.http("PUT", f"/v2/meetings/{self.mid}/preparation", json={**preparation,
            "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"})
        support.value.create_synthetic_live_record(self.live, self.mid)
        self.emit("scope_created", profile=PROFILE,
            workbench_url=f"http://127.0.0.1:{support.APP_PORT}/workbench?meeting_id={self.mid}")
        try:
            self.append(0)
            self.wait_job("discussion_state", 1)
            first = self.checkpoint("natural_old_questions")
            old_questions = [point for point in (first.get("discussion") or {}).get("context", {}).get("open_points", [])
                if isinstance(point, dict) and point.get("entity_type") == "open_question" and point.get("id")]
            if len(old_questions) < 2:
                raise support.AcceptanceStop("expected_old_questions_missing", "first natural state did not form two required old questions; no paid compensation")
            self.append(1)
            self.wait_job("discussion_state", 2)
            second = self.checkpoint("natural_response_then_topic_switch")
            checks, owner = transition_checks(first, second, self.report["sources"][1])
            self.report["responded_point_owner_evidence"] = owner
            jobs, budget = self.collect()
            counts = support.host_budget(jobs)
            checks.update(two_state_host_jobs=counts["discussion_state"] == 2 and counts["host_jobs"] == 2,
                no_automatic_or_manual=counts["automatic"] == counts["manual"] == 0,
                two_actual_upstream_calls=budget["reserved_forward_attempts"] == 2,
                no_retry_or_guard=not counts["retried_jobs"] and not self.report["guard_activations"]
                    and not budget["budget_rejections"] and not budget["upstream_retries"])
            self.report["contract_checks"] = checks
            self.report["contract_failures"] = [key for key, passed in checks.items() if not passed]
            self.report["automated_contract_passed"] = not self.report["contract_failures"]
            self.report["stop_reason"] = "two_natural_state_results_observed" if not self.report["contract_failures"] else "state_transition_contract_failed_without_retry"
        finally:
            self.finish()


support.ContinuousObserver = StateTransitionObserver


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--serve-app", action="store_true")
    modes.add_argument("--serve-proxy", action="store_true")
    parser.add_argument("--profile", choices=(PROFILE, NATURAL_STATE_PROFILE), default=PROFILE)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--pdf-receipt", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    run_dir = (args.run_dir or (NATURAL_STATE_RUN_DIR if args.profile == NATURAL_STATE_PROFILE else DEFAULT_RUN_DIR)).resolve()
    if args.profile == NATURAL_STATE_PROFILE:
        import participation_natural_review_profile as natural
        fixture = natural.read_fixture(args.fixture or natural.DEFAULT_FIXTURE)
        pdf_receipt_path = (args.pdf_receipt or natural.DEFAULT_PDF_RECEIPT).resolve()
        if args.serve_proxy:
            natural_support.serve_proxy(run_dir)
            return 0
        if args.serve_app:
            serve_app(run_dir, fixture=fixture, pdf_receipt_path=pdf_receipt_path)
            return 0
        output = (args.report or run_dir / ("report.json" if args.execute else "plan.json")).resolve()
        report = natural_state_plan(run_dir, fixture, pdf_receipt_path)
        report.update(executed=bool(args.execute), tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            support_tool_sha256=hashlib.sha256(_SUPPORT_PATH.read_bytes()).hexdigest(),
            profile_helper_sha256=natural.sha(Path(natural.__file__).read_bytes()))
        natural_support.fresh_json(output, report)
        if args.execute:
            try:
                observer = natural_state_observer_type(fixture, pdf_receipt_path)
            except (OSError, ValueError, KeyError, TypeError):
                report.update(stop_reason="input_asset_preflight_failed", provider_calls_made=0,
                    stop_diagnostic={"code": "input_asset_preflight_failed"})
                natural_support.update_json(output, report)
                return 1
            return natural_support.execute(run_dir, output, report, observer_type=observer)
        print(json.dumps({"executed": False, "profile": NATURAL_STATE_PROFILE,
            "plan": str(output), "budget": report["budget"]}), flush=True)
        return 0
    if args.serve_proxy:
        support.serve_proxy(run_dir)
        return 0
    if args.serve_app:
        serve_app(run_dir)
        return 0
    output = (args.report or run_dir / ("report.json" if args.execute else "plan.json")).resolve()
    report = plan(run_dir)
    report.update(executed=bool(args.execute), tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        support_tool_sha256=hashlib.sha256(_SUPPORT_PATH.read_bytes()).hexdigest())
    support.fresh_json(output, report)
    if args.execute:
        return support.execute(run_dir, output, report)
    print(json.dumps({"executed": False, "profile": PROFILE, "plan": str(output), "budget": report["budget"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
