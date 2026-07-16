from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "chinese_suggestion_value_gate",
    REPO_ROOT / "tools/chinese_suggestion_value_gate.py",
)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def results_payload():
    return {
        "schema_version": MODULE.RESULTS_SCHEMA,
        "provider": "openai_compatible_gateway",
        "model": "gpt-5.5",
        "gateway_base_url_kind": "remote",
        "is_mock": False,
        "results": [
            {
                "scenario_id": f"trigger-{index:02d}",
                "status": "succeeded",
                "suggestion": "建议确认负责人和回滚条件？",
                "elapsed_ms": 5_000,
                "evidence_ids": [f"evidence:trigger-{index:02d}"],
                "automatic_review": {"ready": True},
            }
            for index in range(1, 21)
        ],
        "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        "privacy_cost_flags": {"llm_called": True, "remote_asr_called": False},
    }


def test_gate_waits_for_all_manual_reviews():
    report = MODULE.build_gate_report(results_payload=results_payload(), annotations_payload=None)

    assert report["verdict"] == "awaiting_manual_review"
    assert report["counts"]["manual_reviews"] == 0


def test_gate_applies_the_product_value_thresholds():
    annotations = {
        "reviews": [
            {
                "scenario_id": f"trigger-{index:02d}",
                "evidence_correct": index <= 18,
                "directly_askable": index <= 16,
                "duplicate_of": "trigger-01" if index in {19, 20} else None,
                "unsupported_claim": False,
            }
            for index in range(1, 21)
        ]
    }

    report = MODULE.build_gate_report(
        results_payload=results_payload(),
        annotations_payload=annotations,
    )

    assert report["verdict"] == "go"
    assert report["counts"]["evidence_correct"] == 18
    assert report["counts"]["directly_askable_timely"] == 16
    assert report["counts"]["duplicates"] == 2
    assert report["counts"]["formal_without_evidence"] == 0
