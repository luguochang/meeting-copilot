import json
import subprocess
import sys

from scripts.demo_pipeline import run_demo_pipeline


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_run_demo_pipeline_builds_traceable_engineering_demo_outputs(tmp_path):
    provider_json = tmp_path / "provider.json"
    analysis_json = tmp_path / "analysis.json"
    golden_json = tmp_path / "golden.json"
    output_dir = tmp_path / "demo"
    _write_json(
        provider_json,
        {
            "text": "payment-gateway 先灰度 10%，如果错误率超过 0.1% 就回滚。这里还没有确认回滚负责人。张三下周三补充兼容性测试用例。监控指标还需要确认 P99 和错误率。",
            "latency_ms": 1800,
            "raw": {"provider": "contract-fixture"},
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 5000,
                    "text": "payment-gateway 先灰度 10%，如果错误率超过 0.1% 就回滚。",
                    "is_final": True,
                },
                {
                    "start_ms": 5000,
                    "end_ms": 9000,
                    "text": "这里还没有确认回滚负责人。",
                    "is_final": True,
                },
                {
                    "start_ms": 9000,
                    "end_ms": 13000,
                    "text": "张三下周三补充兼容性测试用例。",
                    "is_final": True,
                },
                {
                    "start_ms": 13000,
                    "end_ms": 18000,
                    "text": "监控指标还需要确认 P99 和错误率。",
                    "is_final": True,
                },
            ],
        },
    )
    _write_json(
        analysis_json,
        {
            "summary": "讨论 payment-gateway 灰度上线。",
            "meeting_context": {
                "is_engineering_meeting": True,
                "reason": "包含灰度、回滚、监控、测试等上线评审语境。",
            },
            "states": {
                "decision_candidates": [
                    {
                        "id": "decision_001",
                        "statement": "payment-gateway 先灰度 10%",
                        "status": "candidate",
                        "evidence_span_id": "ev_001",
                    }
                ],
                "action_items": [
                    {
                        "id": "action_001",
                        "description": "补充兼容性测试用例",
                        "owner": "张三",
                        "deadline": "下周三",
                        "evidence_span_id": "ev_003",
                    }
                ],
                "risks": [
                    {
                        "id": "risk_001",
                        "description": "错误率超过 0.1% 需要回滚",
                        "evidence_span_id": "ev_001",
                    }
                ],
                "open_questions": [
                    {
                        "id": "question_001",
                        "question": "谁负责回滚？",
                        "evidence_span_id": "ev_002",
                    }
                ],
            },
            "suggestion_cards": [
                {
                    "id": "card_001",
                    "type": "owner_gap",
                    "suggested_question": "是否需要确认回滚负责人？",
                    "evidence_span_id": "ev_002",
                },
                {
                    "id": "card_002",
                    "type": "metric_monitoring_gap",
                    "suggested_question": "是否需要确认 P99 和错误率的监控阈值？",
                    "evidence_span_id": "ev_004",
                },
            ],
        },
    )
    _write_json(
        golden_json,
        {
            "technical_entities": [
                {"normalized": "payment-gateway"},
                {"normalized": "10%"},
                {"normalized": "0.1%"},
                {"normalized": "P99"},
            ]
        },
    )
    glossary_json = tmp_path / "glossary.json"
    _write_json(
        glossary_json,
        {
            "terms": [
                {"canonical": "payment-gateway", "aliases": ["payment-gateway"]},
                {"canonical": "P99", "aliases": ["P99"]},
            ]
        },
    )

    result = run_demo_pipeline(
        provider_json_path=provider_json,
        audio_path="fixture.wav",
        duration_seconds=18.0,
        analysis_json_path=analysis_json,
        golden_path=golden_json,
        glossary_path=glossary_json,
        output_dir=output_dir,
    )

    assert result.evaluation["passes_minimum_gate"] is True
    assert result.evaluation["state_event_count"] == 6
    assert {event["target_type"] for event in result.events} == {
        "ActionItem",
        "DecisionCandidate",
        "OpenQuestion",
        "Risk",
        "SuggestionCard",
    }
    assert (output_dir / "transcript-report.json").exists()
    assert (output_dir / "analysis.json").exists()
    assert (output_dir / "events.json").exists()
    assert (output_dir / "evaluation.json").exists()
    assert result.transcript_report["normalized_text"]


def test_run_demo_pipeline_keeps_non_engineering_cards_empty(tmp_path):
    provider_json = tmp_path / "provider.json"
    analysis_json = tmp_path / "analysis.json"
    output_dir = tmp_path / "demo"
    _write_json(
        provider_json,
        {
            "text": "今天聊股票投资和宏观经济。",
            "latency_ms": 900,
            "raw": {"provider": "contract-fixture"},
        },
    )
    _write_json(
        analysis_json,
        {
            "summary": "讨论股票投资。",
            "meeting_context": {
                "is_engineering_meeting": False,
                "reason": "没有软件工程交付语境。",
            },
            "states": {
                "decision_candidates": [],
                "action_items": [],
                "risks": [],
                "open_questions": [],
            },
            "suggestion_cards": [],
        },
    )

    result = run_demo_pipeline(
        provider_json_path=provider_json,
        audio_path="fixture.wav",
        duration_seconds=5.0,
        analysis_json_path=analysis_json,
        golden_path=None,
        glossary_path=None,
        output_dir=output_dir,
    )

    assert result.evaluation["is_engineering_meeting"] is False
    assert result.evaluation["suggestion_card_count"] == 0
    assert result.evaluation["passes_minimum_gate"] is True


def test_demo_pipeline_cli_runs_as_direct_script():
    result = subprocess.run(
        [sys.executable, "scripts/demo_pipeline.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Run a local Meeting Copilot demo pipeline" in result.stdout
    assert "--glossary" in result.stdout
