"""Exercise real candidate HTTP memory jobs with disposable synthetic sources.

Default mode disables reconciliation before confirming text: zero model calls.
--live enables one newly confirmed explicit-declaration version, allowing one
bounded Pi job. Polling never retries or confirms a memory. No audio is captured.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = '术语纠正：将“托克吹斯”改为“Talktrace”。\n我希望回答简短，并且低频提醒。'


def run(port: int, output: Path, live: bool) -> dict:
    import httpx

    report = {
        "run_id": "memory_acceptance_" + uuid.uuid4().hex[:10],
        "scope": "isolated candidate HTTP; synthetic sources only; no microphone",
        "live_job_authorized": live,
        "maximum_new_paid_jobs": 1 if live else 0,
        "automatic_harness_retries": 0,
        "checks": {},
    }

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
        def call(method, path, **kwargs):
            save()
            response = client.request(method, path, **kwargs)
            if response.is_error:
                report["harness_failure"] = {"method": method, "path": path, "status": response.status_code}
                save()
                raise RuntimeError(f"{method} {path} returned {response.status_code}")
            return response.json()

        project = call("POST", "/v2/knowledge/projects", json={"name": "验收自动记忆 " + report["run_id"]})["project"]
        project_id = project["id"]
        report["project_id"] = project_id
        settings_path = f"/v2/knowledge/projects/{project_id}/memory-reconcile-settings"
        setting = call("GET", settings_path)["settings"]
        setting = call("PUT", settings_path, json={"mode": "off", "expected_revision": setting["revision"]})["settings"]

        def jobs():
            return call("GET", "/v2/knowledge/memory-reconcile/jobs", params={"project_id": project_id, "limit": 20})["items"]

        def confirmed(title, content):
            created = call("POST", "/v2/knowledge/documents/manual", json={"project_id": project_id, "title": title, "content": content})
            document = created["document"]
            document = call("POST", f"/v2/knowledge/documents/{document['id']}/confirm", json={
                "version_id": created["version"]["id"], "expected_revision": document["revision"]})["document"]
            return document

        confirmed("合成业务条件", "客户A预算120元，压测尚未完成，不能承诺上线。")
        report["checks"]["ordinary_text_does_not_enqueue_while_off"] = not jobs()
        document = confirmed("合成本人明确纠正与偏好", DECLARATIONS)
        report["document_id"] = document["id"]
        report["checks"]["off_blocks_explicit_declaration_job"] = not jobs()
        report["settings"] = setting
        if live:
            setting = call("PUT", settings_path, json={"mode": "explicit_only", "expected_revision": setting["revision"]})["settings"]
            report["checks"]["enable_does_not_scan_old_confirmed_versions"] = not jobs()
            updated = call("POST", f"/v2/knowledge/documents/{document['id']}/versions", json={
                "content": DECLARATIONS + "\n合成验收资料：新版由本人明确保存。",
                "expected_revision": document["revision"], "base_version_id": document["active_version_id"]})
            call("POST", f"/v2/knowledge/documents/{document['id']}/confirm", json={
                "version_id": updated["version"]["id"], "expected_revision": updated["document"]["revision"]})
            report["source_version_id"] = updated["version"]["id"]
            started = time.monotonic()
            latest = []
            while time.monotonic() - started < 50:
                latest = jobs()
                report["jobs"] = latest
                save()
                if latest and all(item["status"] not in {"pending", "running"} for item in latest):
                    break
                time.sleep(1)
            memories = call("GET", "/v2/knowledge/memories", params={"scope": "project", "scope_id": project_id})["items"]
            report["memories"] = memories
            report["settings"] = setting
            report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            report["checks"]["one_new_version_one_job"] = len(latest) == 1 and latest[0]["source_version_id"] == updated["version"]["id"]
            report["checks"]["real_pi_job_succeeded_once"] = bool(latest and latest[0]["status"] == "succeeded" and latest[0]["attempts"] == 1)
            report["checks"]["ai_proposals_require_confirmation"] = bool(memories and all(item["status"] == "candidate" and item["origin"] == "ai" for item in memories))
            report["checks"]["result_ids_match_real_candidates"] = bool(latest and set(latest[0]["result_memory_ids"]) == {item["id"] for item in memories})
    save()
    return {key: report[key] for key in ("run_id", "project_id", "checks")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.port == 8765 or not 1024 <= args.port <= 65535 or not output.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("use a separate candidate port and ignored artifacts output")
    if output.exists():
        parser.error("preserve the previous report; choose a fresh output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = run(args.port, output, args.live)
    print(json.dumps(report, ensure_ascii=True))
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
