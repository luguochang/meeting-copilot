"""Exercise the file-ASR producer/worker CLI boundary without loading a model."""

from __future__ import annotations

import argparse
import ast
import copy
import json
from pathlib import Path
import subprocess
import wave

import pytest

from meeting_copilot_web_mvp import batch_transcribe


REPO_ROOT = Path(__file__).resolve().parents[4]
WORKERS = REPO_ROOT / "code/asr_runtime/scripts"


def _worker_parser(worker: Path):
    """Compile only the real entry's argparse statements, excluding inference.

    This deliberately does not import or execute the worker module. A future
    CLI mismatch must fail before any audio read, subprocess, Torch import or
    model construction, even if the worker's inference implementation changes.
    """
    tree = ast.parse(worker.read_text(encoding="utf-8"), filename=str(worker))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    statements = []
    for original in main.body:
        statement = copy.deepcopy(original)
        statements.append(statement)
        if (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "parse_args"
        ):
            statement.value.args = [ast.Name(id="argv", ctx=ast.Load())]
            break
    else:
        raise AssertionError("worker has no explicit argparse boundary")
    statements.append(ast.Return(value=ast.Name(id="args", ctx=ast.Load())))
    function = ast.FunctionDef(
        name="parse_worker_argv",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="argv")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=statements,
        decorator_list=[],
    )
    parser_module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {"argparse": argparse, "Path": Path, "_parse_chunk_size": str}
    exec(compile(parser_module, str(worker), "exec"), namespace)
    return namespace["parse_worker_argv"]


def _generated_command(monkeypatch, tmp_path: Path, worker: Path):
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\0" * 640)
    components = {name: {"path": tmp_path / name} for name in batch_transcribe._COMPONENT_ENV}
    components["worker"] = {"path": worker}
    monkeypatch.setattr(batch_transcribe, "_resolve_runtime_components", lambda: {"packaged_mode": False, "components": components})
    monkeypatch.setattr(batch_transcribe, "capability_status", lambda: {"available": True})
    monkeypatch.setattr(batch_transcribe, "_windows_resource_guard_enabled", lambda: False)
    monkeypatch.setattr(batch_transcribe, "ensure_wav_16k_mono", lambda path: path)
    commands = []

    def never_spawn(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"items": [{"text": "合成契约样本。"}]}), stderr="")

    monkeypatch.setattr(batch_transcribe.subprocess, "run", never_spawn)
    assert batch_transcribe.transcribe_file_report(audio)["text"] == "合成契约样本。"
    assert len(commands) == 1
    return commands[0], audio, components


def test_generated_batch_command_matches_current_source_worker_parser(monkeypatch, tmp_path):
    worker = WORKERS / "transcribe_funasr.py"
    command, audio, components = _generated_command(monkeypatch, tmp_path, worker)
    parsed = _worker_parser(worker)(command[2:])
    assert command[1] == str(worker)
    assert parsed.audio == [audio]
    assert parsed.offline_batch is True
    assert parsed.model == str(components["offline_model"]["path"])
    assert parsed.vad_model == str(components["vad_model"]["path"])
    assert parsed.punc_model == str(components["punc_model"]["path"])
    assert parsed.device == "cpu"


def test_legacy_batch_worker_does_not_support_current_batch_command(monkeypatch, tmp_path, capsys):
    worker = WORKERS / "funasr_batch_worker.py"
    command, _audio, _components = _generated_command(monkeypatch, tmp_path, worker)
    with pytest.raises(SystemExit) as rejected:
        _worker_parser(worker)(command[2:])
    assert rejected.value.code == 2
    assert "--audio" in capsys.readouterr().err
