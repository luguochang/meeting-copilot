"""Small real-Torch fixtures only: no AutoModel construction or ASR weights."""

from collections import OrderedDict
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest
import torch

from scripts import funasr_cpu_checkpoint_loader as helper


class _TinyEncoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = torch.nn.Linear(4, 4)

    def forward(self, values, lengths):
        return self.linear(values).tanh(), lengths, None


def _tiny_known_model():
    # The production class identity and punc_forward are real. Bypass its normal
    # constructor and attach tiny layers; no AutoModel or model assets are used.
    from funasr.models.ct_transformer.model import CTTransformer

    torch.manual_seed(310001)
    model = CTTransformer.__new__(CTTransformer)
    torch.nn.Module.__init__(model)
    model.embed = torch.nn.Embedding(8, 4)
    model.encoder = _TinyEncoder()
    model.decoder = torch.nn.Linear(4, 3)
    return model.eval()


@pytest.fixture
def loaders(monkeypatch):
    auto = importlib.import_module("funasr.auto.auto_model")
    upstream = importlib.import_module("funasr.train_utils.load_pretrained_model")
    original = upstream.load_pretrained_model
    monkeypatch.setattr(auto, "load_pretrained_model", original)
    # These are actual installed versions and actual wheel source hash, not fake
    # providers or a skip on a dependency gate. All parity tests run this path.
    assert torch.__version__ == helper.TORCH_VERSION
    assert hashlib.sha256(Path(original.__code__.co_filename).read_bytes()).hexdigest() == helper.UPSTREAM_LOADER_SHA256
    result = helper.install_funasr_cpu_checkpoint_loader(auto.AutoModel)
    assert result == helper.LoaderInstallResult(True, "installed")
    assert auto.load_pretrained_model is not original
    assert upstream.load_pretrained_model is original
    return original, auto.load_pretrained_model


def _changed_state(model):
    state = model.state_dict()
    for index, tensor in enumerate(state.values()):
        tensor.copy_(torch.arange(tensor.numel(), dtype=tensor.dtype).reshape(tensor.shape) / 16 + index)
    return state


def _bits(tensor):
    return tensor.detach().contiguous().numpy().tobytes()


def _state_bits(model):
    return {key: (tensor.dtype, tuple(tensor.shape), _bits(tensor)) for key, tensor in model.state_dict().items()}


def _tensor_records(value):
    if isinstance(value, torch.Tensor):
        return [(value, value._version, value.untyped_storage()._cdata, _bits(value))]
    if isinstance(value, dict):
        return [record for child in value.values() for record in _tensor_records(child)]
    return []


def _track_checkpoint_reads(monkeypatch):
    real_load = torch.load
    captures = []

    def tracked_load(*args, **kwargs):
        value = real_load(*args, **kwargs)
        captures.append((value, _tensor_records(value)))
        return value

    monkeypatch.setattr(torch, "load", tracked_load)
    return captures


def _assert_sources_unchanged(captures):
    for _, records in captures:
        for tensor, version, storage_id, bits in records:
            assert tensor._version == version
            assert tensor.untyped_storage()._cdata == storage_id
            assert _bits(tensor) == bits


@pytest.mark.parametrize("wrapper", ["plain", "state_dict", "model_state_dict", "model", "nested"])
def test_original_bit_exact_forward_and_source_unchanged(loaders, monkeypatch, tmp_path, wrapper, capsys):
    original, controlled = loaders
    state = _changed_state(_tiny_known_model())
    checkpoint = state
    if wrapper == "nested":
        checkpoint = {"state_dict": {"model_state_dict": {"model": state}}}
    elif wrapper != "plain":
        checkpoint = {wrapper: state, "epoch": 7}
    path = tmp_path / "tiny.pt"
    torch.save(checkpoint, path)
    before = hashlib.sha256(path.read_bytes()).digest()
    expected, actual = _tiny_known_model(), _tiny_known_model()
    captures = _track_checkpoint_reads(monkeypatch)
    assert original(path, expected, scope_map=[]) is None
    assert controlled(path, actual, scope_map=[]) is None
    assert _state_bits(actual) == _state_bits(expected)
    tokens, lengths = torch.tensor([[0, 2, 4]]), torch.tensor([3])
    with torch.no_grad():
        expected_output = expected.punc_forward(tokens, lengths)[0]
        actual_output = actual.punc_forward(tokens, lengths)[0]
    assert _bits(actual_output) == _bits(expected_output)
    assert hashlib.sha256(path.read_bytes()).digest() == before
    _assert_sources_unchanged(captures)
    # Loaded model parameters retain their own storage rather than checkpoint
    # aliases, unlike assign=True. The original source stays independent.
    sources = {record[2] for record in captures[-1][1]}
    assert all(tensor.untyped_storage()._cdata not in sources for tensor in actual.state_dict().values())
    assert "status=optimized code=ordinary_strict_copy model=CTTransformer" in capsys.readouterr().err


@pytest.mark.parametrize("scope_map", [["seed.", ""], "seed.,"])
@pytest.mark.parametrize("excludes", [["decoder."], "decoder."])
def test_mapping_exclusion_shape_and_missing_retained_exactly(loaders, tmp_path, scope_map, excludes, capsys):
    original, controlled = loaders
    initial = _tiny_known_model()
    state = _changed_state(_tiny_known_model())
    state.pop("encoder.linear.bias")
    state["embed.weight"] = torch.ones(2, 2)
    state["unused"] = torch.ones(2)
    path = tmp_path / "mapped.pt"
    torch.save(OrderedDict(("seed." + key, value) for key, value in state.items()), path)
    expected, actual = _tiny_known_model(), _tiny_known_model()
    original(path, expected, scope_map=copy.copy(scope_map), excludes=excludes)
    controlled(path, actual, scope_map=copy.copy(scope_map), excludes=excludes)
    assert _state_bits(actual) == _state_bits(expected)
    for key in ("embed.weight", "encoder.linear.bias", "decoder.weight", "decoder.bias"):
        assert _bits(actual.state_dict()[key]) == _bits(initial.state_dict()[key])
    diagnostic = capsys.readouterr().err
    assert "missing_keys=1 mismatched_keys=1 excluded_keys=2" in diagnostic


def test_default_module_prefix_and_scope_list_side_effect_match(loaders, tmp_path):
    original, controlled = loaders
    state = _changed_state(_tiny_known_model())
    path = tmp_path / "prefixed.pt"
    torch.save({"module." + key: value for key, value in state.items()}, path)
    expected, actual = _tiny_known_model(), _tiny_known_model()
    expected_scope, actual_scope = [], []
    original(path, expected, scope_map=expected_scope)
    controlled(path, actual, scope_map=actual_scope)
    assert expected_scope == actual_scope == ["module.", "None"]
    assert _state_bits(actual) == _state_bits(expected)


def test_strict_shape_error_is_not_retried(loaders, monkeypatch, tmp_path):
    original, controlled = loaders
    state = _changed_state(_tiny_known_model())
    state["embed.weight"] = torch.ones(2, 2)
    path = tmp_path / "shape.pt"
    torch.save(state, path)
    captures = _track_checkpoint_reads(monkeypatch)
    with pytest.raises(RuntimeError, match="size mismatch") as expected_error:
        original(path, _tiny_known_model(), scope_map=[], ignore_init_mismatch=False)
    with pytest.raises(RuntimeError, match="size mismatch") as actual_error:
        controlled(path, _tiny_known_model(), scope_map=[], ignore_init_mismatch=False)
    assert str(actual_error.value) == str(expected_error.value)
    assert len(captures) == 2
    _assert_sources_unchanged(captures)


def test_large_copy_boundary_has_zero_checkpoint_tensor_deepcopies(loaders, monkeypatch, tmp_path):
    original, controlled = loaders
    model = _tiny_known_model()
    path = tmp_path / "copy-boundary.pt"
    torch.save(_changed_state(model), path)
    real_deepcopy = torch.Tensor.__deepcopy__
    copies = []

    def counted_copy(tensor, memo):
        copies.append(tensor.untyped_storage().nbytes())
        return real_deepcopy(tensor, memo)

    monkeypatch.setattr(torch.Tensor, "__deepcopy__", counted_copy)
    original(path, _tiny_known_model(), scope_map=[])
    assert len(copies) == len(model.state_dict())
    original_copied_bytes = sum(copies)
    assert original_copied_bytes > 0
    copies.clear()
    controlled(path, _tiny_known_model(), scope_map=[])
    assert copies == []


@pytest.mark.parametrize("unsafe", ["pre_hook", "post_hook", "custom_loader", "hidden_child_loader"])
def test_unsafe_hook_or_loader_falls_back_before_source_write(loaders, monkeypatch, tmp_path, unsafe, capsys):
    _, controlled = loaders
    model = _tiny_known_model()

    def mutate_source(module, state, prefix, *args):
        for value in state.values():
            if isinstance(value, torch.Tensor) and value.is_floating_point():
                value.data.add_(16)  # Would bypass a version-only mutation check.

    def custom_load(module, state, prefix, metadata, strict, missing, unexpected, errors):
        mutate_source(module, state, prefix)
        return torch.nn.Module._load_from_state_dict(module, state, prefix, metadata, strict, missing, unexpected, errors)

    if unsafe == "pre_hook":
        model.register_load_state_dict_pre_hook(mutate_source)
    elif unsafe == "post_hook":
        model.register_load_state_dict_post_hook(lambda module, incompatible: None)
    elif unsafe == "custom_loader":
        model._load_from_state_dict = types.MethodType(custom_load, model)
    else:
        model.encoder.linear._load_from_state_dict = types.MethodType(custom_load, model.encoder.linear)
        model.encoder.named_modules = types.MethodType(lambda module, *args, **kwargs: iter([]), model.encoder)
    path = tmp_path / "unsafe.pt"
    torch.save(_changed_state(_tiny_known_model()), path)
    captures = _track_checkpoint_reads(monkeypatch)
    controlled(path, model, scope_map=[])
    assert len(captures) == 1  # Rejected before any optimized checkpoint decode.
    _assert_sources_unchanged(captures)
    assert "status=stock code=unsafe_module_tree" in capsys.readouterr().err


def test_unknown_model_and_future_swap_use_stock(loaders, monkeypatch, tmp_path, capsys):
    _, controlled = loaders
    model = torch.nn.Linear(2, 2)
    path = tmp_path / "unknown.pt"
    torch.save(model.state_dict(), path)
    controlled(path, model, scope_map=[])
    assert "status=stock code=unrecognized_model" in capsys.readouterr().err
    known = _tiny_known_model()
    path = tmp_path / "swap.pt"
    torch.save(known.state_dict(), path)
    torch.__future__.set_swap_module_params_on_conversion(True)
    try:
        controlled(path, known, scope_map=[])
    finally:
        torch.__future__.set_swap_module_params_on_conversion(False)
    assert "status=stock code=unsafe_module_tree" in capsys.readouterr().err


@pytest.mark.parametrize("gate", ["funasr_version", "torch_version", "loader_hash", "binding"])
def test_install_gates_leave_original_binding_untouched(monkeypatch, gate, capsys):
    auto = importlib.import_module("funasr.auto.auto_model")
    original = importlib.import_module("funasr.train_utils.load_pretrained_model").load_pretrained_model
    monkeypatch.setattr(auto, "load_pretrained_model", original)
    if gate == "funasr_version":
        real_version = helper.importlib.metadata.version
        monkeypatch.setattr(helper.importlib.metadata, "version", lambda name: "0.0.0" if name == "funasr" else real_version(name))
    elif gate == "torch_version":
        monkeypatch.setattr(torch, "__version__", "2.12.1+cuda")
    elif gate == "loader_hash":
        monkeypatch.setattr(helper, "UPSTREAM_LOADER_SHA256", "0" * 64)
    else:
        monkeypatch.setattr(auto, "load_pretrained_model", lambda *args, **kwargs: None)
        original = auto.load_pretrained_model
    result = helper.install_funasr_cpu_checkpoint_loader(auto.AutoModel)
    assert result.installed is False
    assert auto.load_pretrained_model is original
    assert "status=stock" in capsys.readouterr().err


def test_install_idempotence_and_original_function_remains_available(loaders, capsys):
    original, controlled = loaders
    auto = importlib.import_module("funasr.auto.auto_model")
    assert helper.install_funasr_cpu_checkpoint_loader(auto.AutoModel) == helper.LoaderInstallResult(True, "already_installed")
    assert auto.load_pretrained_model is controlled
    assert controlled.__wrapped__ is original
    assert "code=already_installed" in capsys.readouterr().err


def test_metadata_copy_contains_no_tensor_copy_and_rejects_unsafe_values():
    original = OrderedDict({"": {"version": 1, "nested": ["fixture", {"flag": False}]}})
    valid, cloned = helper._copy_plain_metadata(original, [100])
    assert valid and cloned == original
    cloned[""]["nested"][1]["flag"] = True
    assert original[""]["nested"][1]["flag"] is False
    assert helper._copy_plain_metadata({"tensor": torch.ones(1)}, [100]) == (False, None)
    recursive = {}
    recursive["cycle"] = recursive
    assert helper._copy_plain_metadata(recursive, [100]) == (False, None)


@pytest.mark.parametrize("dtype", [torch.float16, torch.float32, torch.float64])
def test_destination_dtype_and_requires_grad_are_preserved(loaders, tmp_path, dtype):
    original, controlled = loaders
    path = tmp_path / "dtype.pt"
    torch.save(_changed_state(_tiny_known_model()), path)
    expected, actual = _tiny_known_model().to(dtype), _tiny_known_model().to(dtype)
    expected.decoder.weight.requires_grad_(False)
    actual.decoder.weight.requires_grad_(False)
    original(path, expected, scope_map=[])
    controlled(path, actual, scope_map=[])
    assert _state_bits(actual) == _state_bits(expected)
    assert all(value.dtype == dtype for value in actual.state_dict().values())
    assert actual.decoder.weight.requires_grad is False


def test_nonplain_destination_and_extra_state_are_rejected_before_loading():
    model = _tiny_known_model()
    assert helper._safe_module_tree(model, torch)
    model.decoder = torch.nn.LazyLinear(3)
    assert not helper._safe_module_tree(model, torch)
    model = _tiny_known_model().to("meta")
    assert not helper._safe_module_tree(model, torch)
    model = _tiny_known_model()
    model.set_extra_state = types.MethodType(lambda module, state: None, model)
    assert not helper._safe_module_tree(model, torch)


def test_import_has_no_provider_or_torch_side_effects():
    scripts = Path(helper.__file__).parent
    code = f"import sys;sys.path.insert(0,{str(scripts)!r});import funasr_cpu_checkpoint_loader;print('torch' in sys.modules,'funasr' in sys.modules)"
    result = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False False"
    assert result.stderr == ""


def test_stub_provider_keeps_imports_and_protocol_untouched(monkeypatch, capsys):
    monkeypatch.setattr(helper.importlib, "import_module", lambda name: pytest.fail("Stub should not import provider dependencies"))
    assert helper.install_funasr_cpu_checkpoint_loader(object) == helper.LoaderInstallResult(False, "unsupported_provider")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "funasr_cpu_checkpoint_loader status=stock code=unsupported_provider"


@pytest.mark.parametrize("entry", ["offline_refiner", "batch", "transcribe", "offline_batch", "file_streaming"])
def test_cpu_worker_entries_install_before_stub_construction(monkeypatch, tmp_path, capsys, entry):
    calls = []

    class FakeAutoModel:
        def __init__(self, **kwargs):
            assert calls == ["install"]
            calls.append("construct")

        def generate(self, **kwargs):
            return [{"text": "synthetic"}]

    def install(provider):
        assert provider is FakeAutoModel
        calls.append("install")
        return helper.LoaderInstallResult(False, "unsupported_provider")

    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeAutoModel))
    if entry == "offline_refiner":
        from scripts import funasr_offline_refiner_worker as worker

        monkeypatch.setattr(worker, "install_funasr_cpu_checkpoint_loader", install)
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"command":"shutdown"}\n'))
        worker.main(["--model", str(tmp_path / "model"), "--vad-model", str(tmp_path / "vad"),
                     "--punc-model", str(tmp_path / "punc")])
        assert json.loads(capsys.readouterr().out)["event_type"] == "ready"
    elif entry == "batch":
        from scripts import funasr_batch_worker as worker

        monkeypatch.setattr(worker, "install_funasr_cpu_checkpoint_loader", install)
        monkeypatch.setattr(sys, "argv", ["worker", "--audio", str(tmp_path / "synthetic.wav")])
        worker.main()
        assert json.loads(capsys.readouterr().out)["text"] == "synthetic"
    else:
        from scripts import transcribe_funasr as worker

        monkeypatch.setattr(worker, "install_funasr_cpu_checkpoint_loader", install)
        if entry == "transcribe":
            assert worker.transcribe(tmp_path / "synthetic.wav", "fake-model", "cpu")["text"] == "synthetic"
        elif entry == "offline_batch":
            worker.transcribe_offline_batch(audio_paths=[], model_name="fake-model", device="cpu")
        else:
            # Direct loader retains CPU policy. Existing transcribe tests exercise
            # all guarded local batch paths with their fake provider fixtures.
            model_dir = tmp_path / "synthetic-model"
            model_dir.mkdir()
            (model_dir / "model.pt").write_bytes(b"synthetic-stub-not-a-checkpoint")
            (model_dir / "config.yaml").write_text("model: synthetic\n", encoding="utf-8")
            worker._load_streaming_model(model_name="fake-model", local_model_dir=model_dir, device="cpu")
    assert calls == ["install", "construct"]
