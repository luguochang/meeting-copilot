"""Thin, device-free natural fixture adapter for the existing continuous runner.

No legacy postcheck modules are imported: some alter their support module at
import time. This module owns no server, model transport, timer or new budget.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "natural-review-short"
DEFAULT_RUN_DIR = ROOT / "artifacts/realtime-participation-20261007/natural-review-short-r01"
DEFAULT_FIXTURE = ROOT / "artifacts/realtime-participation-20261007/representative-review-natural-fixture-20261008.json"
DEFAULT_PDF_RECEIPT = ROOT / "artifacts/realtime-participation-20261007/representative-product-pdf-fixture-20261008.json"
FIXTURE_SHA = "7aca2b0e23e33af208490943485cd15b8e3f8ed048e5834cd29fd28b0dc2425a"
PDF_RECEIPT_SHA = "f42ce4730b17ba85aa45ad011279d63f52eb8d8b10c310a236d71932bb7dffad"
OPERATIONS = {"append_block", "enable_material", "save_material_version", "record_save_only_checkpoint",
    "record_actual_enabled_scope", "observe_natural_state_and_automatic"}
INTENTS = ("ask_question", "compare_options", "formulate_response")
TOOL_NAMES = {"submit_intervention", "keep_silent", "read_project_state", "search_project_documents",
    "read_realtime_context", "search_prior_evidence", "read_transcript_span"}
SIGNAL_FIELDS = {"meeting_id", "stage", "signal", "nonce"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_fixture(path: Path) -> dict:
    path = path.resolve()
    raw = path.read_bytes()
    if sha(raw) != FIXTURE_SHA:
        raise ValueError("natural fixture does not match the frozen source")
    data = json.loads(raw)
    blocks, steps = data["blocks"], data["adapter_plan"]["steps"]
    if (data.get("schema_version") != "meeting-copilot.representative-review-fixture.v2"
            or data.get("public_synthetic") is not True or data.get("audio_devices_authorized") is not False
            or len(blocks) != 4 or len(steps) != 4):
        raise ValueError("unsupported natural fixture")
    for block, step in zip(blocks, steps, strict=True):
        if (set(block) != {"block_id", "phase", "turns"} or block["block_id"] != step["block_id"]
                or len(block["turns"]) != 3 or step.get("manual_request") != ""
                or any(set(turn) != {"turn_id", "text"} or not isinstance(turn["text"], str)
                    or not turn["text"].strip() for turn in block["turns"])
                or any(op["operation"] not in OPERATIONS for op in step["ordered_operations"])):
            raise ValueError("natural fixture contains unsupported input or action")
    if tuple(step["manual_intent"] for step in steps[1:]) != INTENTS:
        raise ValueError("natural fixture must contain the three explicit intents")
    for material in data["materials"]:
        for version in material["versions"]:
            if sha(version["content"].encode()) != version["utf8_sha256"]:
                raise ValueError("material source changed")
    return {"path": path, "sha256": sha(raw), "data": data}


def read_pdf_receipt(path: Path, fixture: dict) -> dict:
    raw = path.resolve().read_bytes()
    if sha(raw) != PDF_RECEIPT_SHA:
        raise ValueError("PDF receipt does not match the frozen source")
    receipt = json.loads(raw)
    expected = next(m for m in fixture["data"]["materials"] if m["material_id"] == "M_PRODUCT")["versions"][0]
    pdf = receipt["pdf"]
    resolved_pdf = (ROOT / pdf["relative_path"]).resolve()
    if not resolved_pdf.is_relative_to(ROOT.resolve()):
        raise ValueError("PDF fixture path escapes the workspace")
    content = resolved_pdf.read_bytes()
    if (receipt.get("material_id") != "M_PRODUCT" or receipt.get("version") != 1
            or pdf.get("page_count") != 2 or not content.startswith(b"%PDF-")
            or sha(content) != pdf["sha256"] or receipt["source"]["content"] != expected["content"]
            or receipt["source"]["content_sha256"] != expected["utf8_sha256"]
            or receipt["extraction"].get("all_source_content_preserved") is not True
            or [page["locator"] for page in receipt["extraction"]["pages"]]
                != [{"type": "pdf_page", "page": 1}, {"type": "pdf_page", "page": 2}]):
        raise ValueError("PDF source/lineage contract failed")
    return {"path": path.resolve(), "sha256": sha(raw), "data": receipt, "bytes": content, "resolved_pdf_path": resolved_pdf}


def plan(run_dir: Path, fixture: dict, pdf_receipt: Path | None) -> dict:
    receipt_path = (pdf_receipt or DEFAULT_PDF_RECEIPT).resolve()
    return {"schema_version": "participation_continuous_acceptance.natural.v1", "profile": PROFILE,
        "executed": False, "run_dir": str(run_dir), "runtime": str(run_dir / "runtime"),
        "fixture": {"path": str(fixture["path"]), "sha256": fixture["sha256"]},
        "pdf_receipt_path": str(receipt_path), "pdf_ready_before_execution_required": True,
        "budget": {"host_jobs": 9, "state": 4, "automatic": 2, "manual": 3,
            "forwarded_upstream_posts": 14, "attempts_per_host_job": 1, "retries": 0},
        "app_url": "http://127.0.0.1:8771", "proxy_url": "http://127.0.0.1:8791",
        "timer_gates_overridden": False, "direct_state_or_automatic_admission": False,
        "semantic_fixture_applied": False, "clock_advanced": False, "ttl_shortened": False,
        "audio_capture": False, "audio_asr": False, "audio_device_enumeration": False,
        "input": "12 public synthetic canonical text sources; no ASR or oracle state",
        "synthetic_source_times": "seed sequence offsets are synthetic, not audio or observed wall time",
        "planned_total_seconds_range": fixture["data"]["planned_total_seconds_range"],
        "observed_total_elapsed_ms": None,
        "release_cadence": "Offsets are earliest release times from first actual synthetic source append; ordinary timer/budget checks continue; slow stages move later without rewinding",
        "stages": [{"block_id": step["block_id"], "manual_intent": step["manual_intent"],
            "material_scope": step["material_scope"], "planned_offset_seconds": step["planned_release_offset_seconds"]}
            for step in fixture["data"]["adapter_plan"]["steps"]],
        "ui_signal_contract": {"directory": "ui-signals", "fields": sorted(SIGNAL_FIELDS),
            "bootstrap_seconds": 60, "required_current_dom_seconds": 60,
            "signal_is_not_dom_proof": True, "proof": "exact six-field canonical host trace receipt and monotonic mark"},
        "first_turn_gate": "terminal once with no tool errors; repair finishes in-flight then stops later paid stages",
        "preemption": "actual overlap only; otherwise uncovered without manufacturing another task",
        "manual_value_review": "pending", "evaluation_limits": "Short synthetic scenario only; not 30 minutes, ASR, P95 or real-user usefulness",
        "proxy_credentials": "environment only; never in report or arguments"}


def signal_state(path: Path, expected: dict) -> str:
    try:
        if path.stat().st_size > 1024:
            return "invalid"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError):
        return "invalid"
    return "matched" if isinstance(payload, dict) and set(payload) == SIGNAL_FIELDS and payload == expected else "invalid"


def wait_release(offset_seconds: int, *, origin: float, tick, monotonic=None, sleep=None) -> dict:
    """Honor frozen source cadence with real elapsed time, without moving a clock."""
    if type(offset_seconds) is not int or not 0 <= offset_seconds <= 480:
        raise ValueError("invalid short-fixture release offset")
    monotonic, sleep = monotonic or time.monotonic, sleep or time.sleep
    started = monotonic()
    while True:
        tick()  # A source/unknown-lane/budget failure wins over the scheduled release.
        now = monotonic()
        remaining = origin + offset_seconds - now
        if remaining <= 0:
            return {"planned_offset_seconds": offset_seconds, "actual_release_elapsed_ms": round((now - origin) * 1000),
                "waited_ms": round((now - started) * 1000), "clock_advanced": False}
        sleep(min(.2, remaining))


def dom_observation(card: dict, traces: list[dict]) -> dict:
    """A local inspection signal is not a substitute for the formal UI ACK."""
    from meeting_copilot_web_mvp.coach_ui_render_receipt import normalize_receipt
    receipt = normalize_receipt(card.get("ui_render_receipt"))
    trace = next((item for item in traces if item.get("job_id") == card.get("job_id")), {})
    mark = (trace.get("stages") or {}).get("ui_rendered") or {}
    actual = normalize_receipt(mark.get("attributes"))
    provenance = ((trace.get("execution") or {}).get("provenance") or {}).get("ui_render_ack") or {}
    attributes = provenance.get("attributes") or {}
    matched = bool(receipt and receipt == actual and receipt["job_id"] == card.get("job_id")
        and receipt["decision_id"] == card.get("decision_id") and trace.get("meeting_id") == receipt["meeting_id"]
        and trace.get("job_id") == receipt["job_id"] and provenance.get("status") == "observed"
        and type(mark.get("monotonic_ns")) is int and provenance.get("at_monotonic_ns") == mark["monotonic_ns"]
        and attributes.get("source") == "formal_coach_ui.v1" and attributes.get("event_seq") == receipt["event_seq"]
        and attributes.get("input_version") == receipt["input_version"])
    enqueued = (trace.get("stages") or {}).get("job_queued") or {}
    start = enqueued.get("monotonic_ns")
    end = mark.get("monotonic_ns") if matched else None
    interval = type(start) is int and type(end) is int and 0 <= start <= end
    return {"exact_six_field_dom_ack": matched, "receipt": actual if matched else None,
        "host_mark_monotonic_ns": end, "host_enqueue_monotonic_ns": start,
        "host_monotonic_interval_valid": interval,
        "enqueue_to_dom_ms": round((end - start) / 1e6, 3) if interval else None}


def efficiency(job: dict, outcome: dict) -> dict:
    metrics = (job.get("output") or {}).get("coach", {}).get("agent_metrics") or {}
    if not metrics:
        metrics = ((job.get("output") or {}).get("applied", {}).get("coach_decision") or {}).get("agent_metrics") or {}
    names = [name if name in TOOL_NAMES else "unclassified" for name in metrics.get("tool_names") or []]
    errors = [{"tool": item.get("tool") if item.get("tool") in TOOL_NAMES else "unclassified",
        "code": item.get("code") if item.get("code") in {"evidence_quote_not_verbatim", "document_quote_mismatch",
            "quote_selection_invalid", "quote_selection_same_doc_source", "tool_execution_error"} else "unclassified"}
        for item in metrics.get("tool_errors") or [] if isinstance(item, dict)]
    terminal = "keep_silent" if outcome.get("validated_silence") else "submit_intervention"
    own_valid = outcome.get("valid_intervention") or outcome.get("validated_silence")
    first = bool(own_valid and metrics.get("turns") == 1 and metrics.get("tool_calls") == 1 and names == [terminal] and not errors)
    lookup = bool(own_valid and metrics.get("turns") == 2 and metrics.get("tool_calls") == 2
        and not errors and len(names) == 2 and names[-1] == terminal
        and names[0] in TOOL_NAMES - {"submit_intervention", "keep_silent"})
    return {"turns": metrics.get("turns"), "tool_calls": metrics.get("tool_calls"), "tool_names": names,
        "tool_errors": errors, "first_turn_terminal_no_errors": first,
        "one_lookup_without_error": lookup, "repair_observed": bool(errors),
        "terminal_kind": terminal, "no_rejected_tool_body_saved": True}


def publication_checks(job: dict, card: dict, snapshot: dict, materials: dict) -> dict:
    metadata = job.get("request_context") or {}
    discussion = snapshot.get("discussion") or {}
    context = discussion.get("context") or {}
    versions = {item["version_ids"][item["active_version"]]: item["version_texts"][item["active_version"]]
        for item in materials.values() if item.get("enabled")}
    sources = [source for source in card.get("source_evidence") or [] if source.get("source_type") == "meeting_attachment"]
    return {"typed_participation": card.get("analysis_kind") == "participation",
        "current_topic_basis": bool(card.get("topic_id") and card["topic_id"] == metadata.get("topic_id") == discussion.get("work_item_id")
            and type(card.get("analysis_basis_revision")) is int
            and card["analysis_basis_revision"] == metadata.get("analysis_basis_revision") == context.get("analysis_basis_revision")),
        "only_explicit_active_material_versions": all(source.get("version_id") in versions for source in sources),
        "material_quotes_match_actual_conversion": all(source.get("quote")
            and source["quote"] in versions.get(source.get("version_id"), "") for source in sources)}


def v2_clause_evidence(card: dict, material: dict) -> dict:
    sources = [source for source in card.get("source_evidence") or []
        if source.get("source_type") == "meeting_attachment" and source.get("version_id") == material["version_ids"].get(2)]
    refs = {source.get("source_id") for source in sources}
    facts = [fact for fact in card.get("supporting_facts") or [] if fact.get("source_ref") in refs]
    clause = next(line for line in material["version_texts"][2].splitlines() if line.startswith("P2-v2："))
    related = [fact for fact in facts if fact.get("text") and fact["text"] in clause]
    return {"actual_version_id": material["version_ids"][2], "approved_v2_sources": sources,
        "approved_v2_clause_facts": related, "v2_clause_fact_present": bool(related),
        "advice_applies_reselection_confirmation_and_server_identity_position": "independent_review_pending",
        "incremental_value": "independent_review_pending; scope retraction is not value proof"}


def operation_allowed(method: str, path: str, mid: str) -> bool:
    if method == "GET":
        return True
    if method == "POST" and path == "/v2/meetings":
        return True
    if path.startswith("/v2/material-sets"):
        return (method == "POST" and (path == "/v2/material-sets" or path.endswith(("/attachments", "/versions")))
            or method == "PUT" and path.endswith("/usage"))
    if method == "POST" and path.startswith("/v2/traces/") and path.endswith("/ui-rendered"):
        return bool(mid)
    return bool(mid and path.startswith(f"/v2/meetings/{mid}/") and (
        method == "PUT" and path.endswith("/preparation") or method == "POST" and path.endswith("/coach/request")))


def observer_type(support, fixture: dict, pdf_receipt_path: Path | None):
    """Reuse the actual continuous boundary, repositories and final accounting."""
    pdf = read_pdf_receipt(pdf_receipt_path or DEFAULT_PDF_RECEIPT, fixture)

    class NaturalReviewObserver(support.ContinuousObserver):
        def __init__(self, *args):
            super().__init__(*args)
            self.materials = {}
            self.seq = 0
            self.start_monotonic = time.monotonic()
            self.helper_sha = sha(Path(__file__).read_bytes())
            self.runner_sha = sha(Path(support.__file__).read_bytes())
            self.previous_card = None
            self.input_start_monotonic = None

        def http(self, method, path, **kwargs):
            if not operation_allowed(method, path, self.mid):
                raise support.AcceptanceStop("unexpected_lane", "operation outside natural profile")
            return super().http(method, path, **kwargs)

        def collect(self):
            if (sha(fixture["path"].read_bytes()) != fixture["sha256"]
                    or sha(Path(__file__).read_bytes()) != self.helper_sha
                    or sha(Path(support.__file__).read_bytes()) != self.runner_sha
                    or sha(pdf["path"].read_bytes()) != pdf["sha256"]
                    or sha(pdf["resolved_pdf_path"].read_bytes()) != pdf["data"]["pdf"]["sha256"]):
                raise support.AcceptanceStop("fixture_changed", "frozen natural input changed")
            jobs, boundary = super().collect()
            unexpected = [job["id"] for job in jobs if not support.lane(job)
                and not (job["status"] == "cancelled" and not job.get("attempts")
                    and job.get("error_class") == "synthetic_harness_other_lanes_disabled")]
            if unexpected:
                raise support.AcceptanceStop("unexpected_lane", "unexpected non-profile job admission")
            return jobs, boundary

        def signal(self, stage, name="inspected", maximum=60):
            expected = {"meeting_id": self.mid, "stage": stage, "signal": name, "nonce": uuid.uuid4().hex}
            path = self.run_dir / "ui-signals" / f"{stage}.{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.emit("inspection_signal_requested", inspection_stage=stage, signal=name,
                local_signal_path=str(path), expected_payload=expected, maximum_seconds=maximum,
                workbench_url=f"http://127.0.0.1:{support.APP_PORT}/workbench?meeting_id={self.mid}")
            deadline = time.monotonic() + maximum
            while time.monotonic() < deadline:
                self.collect()
                if time.monotonic() >= deadline:
                    break
                if signal_state(path, expected) == "matched":
                    return True
                time.sleep(.2)
            raise support.AcceptanceStop("ui_ready_timeout" if name == "page_ready" else "dom_receipt_timeout",
                "required page inspection signal missing")

        def append_block(self, block):
            for turn in block["turns"]:
                self.seq += 1
                segment_id = f"natural-public-{uuid.uuid4().hex[:12]}"
                text = turn["text"]  # The only fixture field entering the semantic input.
                self.public_texts[segment_id] = text
                seed = support.value.seed_synthetic_final(self.store, self.live, self.mid,
                    {"segment_id": segment_id, "text": text}, self.seq)
                appended_clock_ns = time.monotonic_ns()
                if self.input_start_monotonic is None:
                    self.input_start_monotonic = appended_clock_ns / 1e9
                    self.report["first_source_append_monotonic_ns"] = appended_clock_ns
                self.report.setdefault("sources", []).append({"block_id": block["block_id"], "turn_id": turn["turn_id"],
                    "segment_id": segment_id, "revision": 1, "text": text, "seed": seed,
                    "synthetic_seq": self.seq, "appended_at_ms": time.time_ns() // 1_000_000,
                    "appended_monotonic_ns": appended_clock_ns})
            self.emit("public_block_appended", block_id=block["block_id"], source_count=3,
                actual_elapsed_ms=round((time.monotonic() - self.start_monotonic) * 1000), model_fields=["turn.text"])

        def spans(self, path, version_id):
            result, offset = [], 0
            while True:
                page = self.http("GET", f"{path}/versions/{version_id}/spans", params={"offset": offset, "limit": 100})
                result.extend(page["items"])
                if not page["has_more"]:
                    return result
                offset = page["next_offset"]

        def upload_materials(self):
            material_set = self.http("POST", "/v2/material-sets")["material_set"]
            self.report["material_set_id"] = sid = material_set["id"]
            for source in fixture["data"]["materials"]:
                content = source["versions"][0]["content"]
                is_pdf = source["material_id"] == "M_PRODUCT"
                uploaded = self.http("POST", f"/v2/material-sets/{sid}/attachments",
                    files={"file": (source["filename"], pdf["bytes"] if is_pdf else content.encode(),
                        "application/pdf" if is_pdf else "text/markdown")}, data={"title": source["filename"],
                        "purpose": "公开合成需求评审资料，非已实现业务证明"})
                aid = uploaded["attachment"]["id"]
                path = f"/v2/material-sets/{sid}/attachments/{aid}"
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    detail = self.http("GET", path)
                    if detail.get("version") is not None:
                        break
                    if (detail.get("job") or {}).get("status") in {"failed", "cancelled"}:
                        raise support.AcceptanceStop("material_conversion_failed", "conversion failed without retry")
                    time.sleep(.1)
                else:
                    raise support.AcceptanceStop("material_conversion_timeout", "conversion timeout")
                spans = self.spans(path, detail["version"]["id"])
                formal_text = ("\n\n" if is_pdf else "\n").join(span["text"] for span in spans)
                if is_pdf:
                    for page in pdf["data"]["extraction"]["pages"]:
                        actual = "\n".join(span["text"] for span in spans if span["locator"].get("page") == page["page"])
                        if actual.replace("\n", "") != page["text"].replace("\n", ""):
                            raise support.AcceptanceStop("material_contract_failed", "formal PDF page conversion mismatch")
                elif formal_text.strip() != content.strip():
                    raise support.AcceptanceStop("material_contract_failed", "formal markdown conversion mismatch")
                if detail["attachment"].get("enabled") or detail["attachment"].get("active_version_id"):
                    raise support.AcceptanceStop("material_contract_failed", "upload implicitly enabled material")
                self.materials[source["material_id"]] = {"attachment_id": aid, "path": path,
                    "version_ids": {1: detail["version"]["id"]}, "version_texts": {1: formal_text},
                    "formal_version": detail["version"], "spans": spans, "enabled": False}
            self.report["materials"] = self.materials
            self.report["pdf_lineage"] = {"receipt_sha256": pdf["sha256"], "pdf_sha256": pdf["data"]["pdf"]["sha256"],
                "resolved_pdf_path": str(pdf["resolved_pdf_path"]),
                "fixture_source_text_sha256": pdf["data"]["source"]["content_sha256"],
                "formal_conversion_page_locators_verified": True,
                "formal_version_content_hash": self.materials["M_PRODUCT"]["formal_version"]["content_hash"],
                "actual_converted_text_sha256": sha(self.materials["M_PRODUCT"]["version_texts"][1].encode()),
                "fixture_text_hash_is_not_used_as_formal_version_hash": True}

        def enable(self, material_id, version):
            material = self.materials[material_id]
            detail = self.http("GET", material["path"])
            before_scope = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")["material_set"]
            prior = self.http("GET", f"/v2/meetings/{self.mid}/snapshot") if self.mid and version == 2 else None
            response = self.http("PUT", material["path"] + "/usage", json={"version_id": material["version_ids"][version],
                "enabled": True, "selected_span_ids": None, "expected_revision": detail["attachment"]["revision"]})
            material.update(enabled=True, active_version=version, usage_ack=response)
            self.emit("material_usage_http_ack", material_id=material_id, version=version,
                version_id=material["version_ids"][version], attachment_revision=response["attachment"]["revision"])
            if prior is not None:
                after = self.http("GET", f"/v2/meetings/{self.mid}/snapshot")
                card = prior.get("coach_intervention") or {}
                now = time.time_ns() // 1_000_000
                inside = type(card.get("valid_until_ms")) is int and now < card["valid_until_ms"]
                self.report["scope_revision_retraction"] = {"coverage": "covered" if card and inside else "uncovered_no_current_ttl",
                    "before_context_revision": before_scope["context_revision"], "after_context_revision": response["material_set"]["context_revision"],
                    "old_card_valid_until_ms": card.get("valid_until_ms"), "observed_at_ms": now,
                    "old_card_not_current": bool(card and (after.get("coach_intervention") or {}).get("decision_id") != card.get("decision_id")),
                    "old_history_retained": bool(card and support.history_contains(after, card)),
                    "causal_boundary": "snapshot immediately before/after usage ACK; earlier basis retraction is separate; concurrent timer cause is not independently excluded"}
                observed = self.report["scope_revision_retraction"]
                if observed["coverage"] == "covered" and not (observed["old_card_not_current"] and observed["old_history_retained"]
                        and observed["after_context_revision"] > observed["before_context_revision"]):
                    raise support.AcceptanceStop("material_contract_failed", "covered scope change violated current/history lifecycle")

        def save_version(self, material_id, version):
            material = self.materials[material_id]
            before_set = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")["material_set"]
            before = self.http("GET", material["path"])
            content = next(v["content"] for m in fixture["data"]["materials"] if m["material_id"] == material_id
                for v in m["versions"] if v["version"] == version)
            saved = self.http("POST", material["path"] + "/versions", json={"content": content,
                "base_version_id": material["version_ids"][1], "expected_revision": before["attachment"]["revision"]})
            after_set = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")["material_set"]
            unchanged = (saved["attachment"]["active_version_id"] == material["version_ids"][1]
                and before_set["context_revision"] == after_set["context_revision"])
            material["version_ids"][version] = saved["version"]["id"]
            material["version_texts"][version] = content
            self.report["save_only_scope"] = {"passed": unchanged, "before_context_revision": before_set["context_revision"],
                "after_context_revision": after_set["context_revision"], "active_version_id": saved["attachment"]["active_version_id"]}
            self.emit("material_v2_save_only_http_ack", passed=unchanged, version_id=saved["version"]["id"])
            if not unchanged:
                raise support.AcceptanceStop("save_changed_active_scope", "save changed the enabled material scope")

        def observe_result(self, job, stage):
            checkpoint = self.checkpoint(stage, job=job)
            outcome = checkpoint["own_outcome"]
            if not (outcome["valid_intervention"] or outcome["validated_silence"]):
                raise support.AcceptanceStop("publication_contract_failed", "own result is not a validated card or silence")
            metric = efficiency(job, outcome)
            entry = {"stage": stage, "job_id": job["id"], "intent": (job.get("request_context") or {}).get("intent"),
                "own_outcome": outcome, "efficiency": metric,
                "dom": "pending_current_dom_inspection" if outcome["valid_intervention"] else "not_required_for_valid_silence"}
            self.report.setdefault("requests", []).append(entry)
            if outcome["valid_intervention"]:
                if not outcome["current_card_for_request"] or not checkpoint["fact_checks"] or not all(
                        item["known_source"] and item["verbatim"] for item in checkpoint["fact_checks"]):
                    raise support.AcceptanceStop("publication_contract_failed", "current card or typed facts invalid")
                entry["publication_checks"] = publication_checks(job, checkpoint["snapshot"]["coach_intervention"],
                    checkpoint["snapshot"], self.materials)
                entry["publication_checks"]["only_frozen_canonical_and_owned_attachment_sources"] = self.verify_sources(
                    checkpoint["snapshot"]["coach_intervention"])
                if not all(entry["publication_checks"].values()):
                    raise support.AcceptanceStop("publication_contract_failed", "current topic/basis or material source invalid")
                self.signal(stage)
                latest = self.checkpoint(stage + "_dom_observed", job=job)
                card = latest["snapshot"].get("coach_intervention") or {}
                observation = dom_observation(card, latest["traces"])
                if (card.get("job_id") != job["id"] or not observation["exact_six_field_dom_ack"]
                        or not observation["host_monotonic_interval_valid"]
                        or type(card.get("valid_until_ms")) is not int or latest["observed_at_ms"] >= card["valid_until_ms"]):
                    raise support.AcceptanceStop("dom_receipt_timeout", "required own current normal-TTL DOM not observed")
                entry["dom"] = observation
                self.previous_card = card
            self.emit("own_result_observed", job_id=job["id"], result="intervention" if outcome["valid_intervention"] else "valid_silence",
                first_turn=metric["first_turn_terminal_no_errors"], lookup=metric["one_lookup_without_error"])
            if not metric["first_turn_terminal_no_errors"] and not metric["one_lookup_without_error"]:
                raise support.AcceptanceStop("first_turn_efficiency_failed", "in-flight result finished; no later paid stage after repair")
            return entry

        def verify_sources(self, card):
            from meeting_copilot_web_mvp.meeting_materials import validate_attachment_evidence
            from meeting_copilot_web_mvp.knowledge_repository import KnowledgeScopeError
            references = set(self.public_texts)
            owned = {item["attachment_id"] for item in self.materials.values()}
            for source in card.get("source_evidence") or []:
                if source.get("source_type") != "meeting_attachment" or source.get("attachment_id") not in owned:
                    return False
                try:
                    with self.store._lock:
                        validate_attachment_evidence(self.store._conn, self.mid, source)
                except (KeyError, KnowledgeScopeError):
                    return False
                references.add(source["source_id"])
            return bool(card.get("supporting_facts")) and all(fact.get("source_ref") in references
                for fact in card["supporting_facts"])

        def wait_automatic2(self):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                jobs, proxy = self.collect()
                matches = sorted((j for j in jobs if support.lane(j) == "automatic"), key=lambda j: (j["created_at_ms"], j["id"]))
                if len(matches) >= 2:
                    job = matches[1]
                    if support.own_failure(job):
                        raise support.AcceptanceStop("own_job_failed", "second automatic failed")
                    traces = self.http("GET", f"/v2/meetings/{self.mid}/traces")["traces"]
                    trace = next((t for t in traces if t.get("job_id") == job["id"]), None)
                    active = job["status"] == "running" and support.in_flight(trace) and any(
                        event["status"] == "reserved" and event["started_at_ms"] >= job["created_at_ms"] for event in proxy["events"])
                    if active or job["status"] == "succeeded":
                        self.report["preemption_coverage"] = {"observed_provider_inflight": bool(active),
                            "automatic_job_id": job["id"], "status_at_manual": job["status"],
                            "coverage": "observed_inflight" if active else "uncovered_overlap_missed"}
                        return job
                time.sleep(.1)
            raise support.AcceptanceStop("observation_timeout", "second natural automatic not observed")

        def run(self):
            from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
            self.report["preflight"] = support.preflight(self.run_dir, self.client)
            identity = self.report["preflight"]["identity"]
            if (identity.get("profile") != PROFILE or identity.get("fixture_sha256") != fixture["sha256"]
                    or identity.get("boundary_tool_sha256") != self.runner_sha or identity.get("profile_helper_sha256") != self.helper_sha):
                raise support.AcceptanceStop("profile_boundary_mismatch", "natural profile identity mismatch")
            self.store = support.value.open_synthetic_store(self.run_dir / "runtime/meeting_copilot.db")
            self.live = SqliteAsrLiveSessionRepository(self.run_dir / "runtime")
            try:
                self.upload_materials()
                self.mid = support.SCOPE_PREFIX + "natural_" + uuid.uuid4().hex[:12]
                self.report["meeting_id"] = self.mid
                material_set = self.http("GET", f"/v2/material-sets/{self.report['material_set_id']}")["material_set"]
                self.http("POST", "/v2/meetings", json={"meeting_id": self.mid,
                    "title": "自然需求评审短场 · 公开合成文本 · 无音频设备",
                    "material_set_id": material_set["id"], "expected_material_set_revision": material_set["revision"]},
                    headers={"Idempotency-Key": self.mid})
                prep = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
                self.report["preparation"] = self.http("PUT", f"/v2/meetings/{self.mid}/preparation",
                    json={**prep, "live_analysis_profile": "participation", "proactive_suggestion_policy": "low_frequency"})
                support.value.create_synthetic_live_record(self.live, self.mid)
                self.emit("scope_created", workbench_url=f"http://127.0.0.1:{support.APP_PORT}/workbench?meeting_id={self.mid}")
                self.signal("bootstrap", "page_ready")  # No canonical input, hence no paid natural task yet.
                for index, (block, step) in enumerate(zip(fixture["data"]["blocks"], fixture["data"]["adapter_plan"]["steps"], strict=True), 1):
                    if self.input_start_monotonic is not None:
                        timing = wait_release(step["planned_release_offset_seconds"], origin=self.input_start_monotonic, tick=self.collect)
                        self.report.setdefault("release_timing", []).append({"block_id": block["block_id"], **timing})
                        self.emit("scheduled_block_release", block_id=block["block_id"], **timing)
                    before = self.http("GET", f"/v2/meetings/{self.mid}/snapshot")
                    before_card = before.get("coach_intervention") or {}
                    for op in step["ordered_operations"]:
                        name = op["operation"]
                        if name == "append_block":
                            self.append_block(block)
                        elif name == "save_material_version":
                            self.save_version(op["material_id"], op["version"])
                        elif name == "enable_material":
                            self.enable(op["material_id"], op["version"])
                        elif name == "record_actual_enabled_scope":
                            actual_set = self.http("GET", f"/v2/material-sets/{material_set['id']}")["material_set"]
                            self.report.setdefault("material_scope_observations", []).append({"block_id": block["block_id"],
                                "material_set": actual_set, "scope": {key: value.get("active_version") for key, value in self.materials.items() if value["enabled"]},
                                "acknowledged_at_ms": time.time_ns() // 1_000_000})
                        elif name == "record_save_only_checkpoint":
                            self.emit("save_only_metadata_checkpoint", passed=self.report["save_only_scope"]["passed"],
                                no_ui_pause=True, no_model_dispatch=True)
                        elif name == "observe_natural_state_and_automatic":
                            pass  # Actual natural observations follow the ordered ACK operations below.
                        else:
                            raise support.AcceptanceStop("unexpected_lane", "unsupported fixture operation")
                    if index == 3:
                        after = self.http("GET", f"/v2/meetings/{self.mid}/snapshot")
                        self.report["prior_block_card_lifecycle"] = {"prior_card_decision_id": before_card.get("decision_id"),
                            "old_card_not_current": bool(before_card and (after.get("coach_intervention") or {}).get("decision_id") != before_card.get("decision_id")),
                            "old_history_retained": bool(before_card and support.history_contains(after, before_card)),
                            "not_attributed_to_scope_alone": True}
                    state = self.wait_job("discussion_state", index)
                    self.checkpoint(f"state{index}_natural_success", job=state)
                    if index == 1:
                        self.observe_result(self.wait_job("automatic", 1), "automatic1")
                    else:
                        auto2 = self.wait_automatic2() if index == 2 else None
                        if auto2 and auto2["status"] == "succeeded":
                            self.observe_result(auto2, "automatic2")
                        manual_id = self.manual(step["manual_intent"], "")
                        if auto2:
                            self.report["overlap_manual_job_id"] = manual_id
                            self.report["overlap_automatic_job_id"] = auto2["id"]
                        manual_job = self.wait_job("manual", index - 1)
                        entry = self.observe_result(manual_job, f"manual{index - 1}")
                        if auto2:
                            prep = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
                            self.http("PUT", f"/v2/meetings/{self.mid}/preparation", json={**prep, "proactive_suggestion_policy": "off"})
                            off = self.checkpoint("policy_off_after_manual_success", job=manual_job)
                            preserved = not entry["own_outcome"]["valid_intervention"] or (off["snapshot"].get("coach_intervention") or {}).get("decision_id") == (
                                entry["own_outcome"]["own_intervention"] or {}).get("decision_id")
                            self.report["policy_off_preserved_manual_card"] = preserved
                            self.emit("cost_control_policy_off", second_auto_actually_observed=True, only_after_own_success_and_dom=True,
                                other_meetings_unchanged=True)
                            if not preserved:
                                raise support.AcceptanceStop("policy_change_withdrew_card", "policy change withdrew this own manual card")
                        if index == 3:
                            self.report["product_v2_clause_increment"] = v2_clause_evidence(
                                entry["own_outcome"]["own_intervention"] or {}, self.materials["M_PRODUCT"])
                        if auto2:
                            actual_auto = self.store.get_job(auto2["id"])
                            actual_manual = self.store.list_jobs(meeting_id=self.mid)
                            manual = next(j for j in actual_manual if j["id"] == entry["job_id"])
                            self.report["preemption_coverage"].update(automatic_final_status=actual_auto["status"],
                                preempted_job_ids=(manual.get("request_context") or {}).get("preempted_job_ids"))
                            if actual_auto["status"] in {"failed", "retry_wait"}:
                                raise support.AcceptanceStop("own_job_failed", "observed automatic ended in a business failure")
                            if actual_auto["status"] not in support.TERMINALS:
                                raise support.AcceptanceStop("observation_timeout", "older automatic did not settle after manual")
                self.report["contract_checks"] = {"four_natural_states": self.report["host_budget"]["discussion_state"] == 4,
                    "three_explicit_empty_request_intents": [r["intent"] for r in self.report["requests"] if r["stage"].startswith("manual")] == list(INTENTS),
                    "save_only_scope_unchanged": self.report["save_only_scope"]["passed"],
                    "formal_two_page_pdf_verified": self.report["pdf_lineage"]["formal_conversion_page_locators_verified"]}
                self.report["automated_contract_passed"] = all(self.report["contract_checks"].values())
                self.report["value_verdict"] = "independent_approved_output_review_pending"
                self.report["semantic_owner_mapping"] = "review_actual_state_points; absent_prior_point_is_uncovered; no_gold_ids_injected"
                self.report["stop_reason"] = "short_stages_observed_value_and_timing_review_pending"
            finally:
                self.report["observed_total_elapsed_ms"] = round((time.monotonic() - self.start_monotonic) * 1000)
                self.report["observed_input_window_elapsed_ms"] = (round((time.monotonic() - self.input_start_monotonic) * 1000)
                    if self.input_start_monotonic is not None else None)
                self.finish()

    return NaturalReviewObserver
