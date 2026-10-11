"""Avoid one checkpoint tensor copy in a narrowly pinned FunASR CPU loader.

The mapping loop is adapted from the installed FunASR 1.3.10 wheel's
``funasr/train_utils/load_pretrained_model.py`` (SHA256 below), distributed by
https://github.com/modelscope/FunASR. This is not a claim that upstream main or
an unverified Git tag has that hash. Changes: omit checkpoint deepcopy, isolate
dictionary metadata, and require known models with ordinary PyTorch copy loads.
Unknown versions, model methods, hooks, tensor types, or loading policies use
the original loader. Importing this helper does not import Torch or FunASR.

MIT License
Copyright (c) 2025 FunASR

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import importlib
import importlib.metadata
from pathlib import Path
import sys
from typing import Any


FUNASR_VERSION = "1.3.10"
TORCH_VERSION = "2.12.1+cpu"
UPSTREAM_LOADER_SHA256 = "772dfe0b02e12d6ce8428f193a0b576ccc32f0d17e4a3c67ac56c25d88fb0c3c"
_KNOWN_MODELS = {
    ("funasr.models.seaco_paraformer.model", "SeacoParaformer"),
    ("funasr.models.ct_transformer.model", "CTTransformer"),
    ("funasr.models.fsmn_vad_streaming.model", "FsmnVADStreaming"),
}
_MARKER = "_meeting_copilot_cpu_checkpoint_loader"


@dataclass(frozen=True)
class LoaderInstallResult:
    installed: bool
    code: str


@dataclass(frozen=True)
class _LoadResult:
    code: str
    tensor_count: int = 0
    unique_storage_bytes: int = 0
    matched_keys: int = 0
    missing_keys: int = 0
    mismatched_keys: int = 0
    excluded_keys: int = 0


def _emit(status: str, code: str, *, model: str = "", result: _LoadResult | None = None) -> None:
    # All labels originate here; no path, source text, API configuration, or
    # arbitrary exception messages are included in the diagnostic protocol.
    fields = ["funasr_cpu_checkpoint_loader", f"status={status}", f"code={code}"]
    if model:
        fields.append(f"model={model}")
    if result is not None:
        fields.extend((
            f"checkpoint_tensors={result.tensor_count}",
            f"checkpoint_storage_bytes={result.unique_storage_bytes}",
            "checkpoint_tensor_deepcopies=0",
            f"matched_keys={result.matched_keys}",
            f"missing_keys={result.missing_keys}",
            f"mismatched_keys={result.mismatched_keys}",
            f"excluded_keys={result.excluded_keys}",
        ))
    print(" ".join(fields), file=sys.stderr, flush=True)


def _is_known_model(model: Any) -> bool:
    cls = type(model)
    key = (cls.__module__, cls.__name__)
    module = sys.modules.get(cls.__module__)
    return key in _KNOWN_MODELS and module is not None and getattr(module, cls.__name__, None) is cls


def _plain_cpu_tensor(value: Any, torch: Any) -> bool:
    return (
        type(value) in (torch.Tensor, torch.nn.Parameter)
        and value.device.type == "cpu"
        and value.layout == torch.strided
        and not value.is_quantized
        and not value.is_meta
    )


def _default_method(obj: Any, name: str, expected: Any) -> bool:
    return getattr(getattr(obj, name, None), "__func__", None) is expected


def _safe_module_tree(model: Any, torch: Any) -> bool:
    """Prove loading only writes destination tensors before sharing a source.

    Version counters are insufficient: a hostile hook could write via .data.
    No custom loader, extra state, or hook is allowed, even when it is otherwise
    valid FunASR code. This intentionally prefers stock fallback to guessing.
    """
    base = torch.nn.Module
    if torch.__future__.get_swap_module_params_on_conversion():
        return False
    pending = [model]
    visited = set()
    while pending:
        module = pending.pop()
        if id(module) in visited:
            continue
        visited.add(id(module))
        if not isinstance(module, base):
            return False
        # Do not trust overridable modules()/named_modules() to expose children.
        children = object.__getattribute__(module, "_modules")
        parameters = object.__getattribute__(module, "_parameters")
        buffers = object.__getattribute__(module, "_buffers")
        if any(type(value) is not dict for value in (children, parameters, buffers)):
            return False
        pending.extend(value for value in children.values() if value is not None)
        for name in ("load_state_dict", "_load_from_state_dict", "state_dict", "_save_to_state_dict",
                     "get_extra_state", "set_extra_state"):
            if not _default_method(module, name, getattr(base, name)):
                return False
        for name in ("_load_state_dict_pre_hooks", "_load_state_dict_post_hooks",
                     "_state_dict_pre_hooks", "_state_dict_hooks"):
            if getattr(module, name, None):
                return False
        # Include non-persistent buffers: unusual destination tensor dispatch
        # should not silently enter this adapter either.
        for value in (*parameters.values(), *buffers.values()):
            if value is not None and not _plain_cpu_tensor(value, torch):
                return False
    return True


def _copy_plain_metadata(value: Any, budget: list[int], depth: int = 0) -> tuple[bool, Any]:
    """Copy only small scalar metadata; never deepcopy tensor storage."""
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        return False, None
    if type(value) in (str, int, float, bool, type(None)):
        return True, value
    if type(value) in (dict, OrderedDict):
        result = type(value)()
        for key, item in value.items():
            if type(key) is not str:
                return False, None
            valid, cloned = _copy_plain_metadata(item, budget, depth + 1)
            if not valid:
                return False, None
            result[key] = cloned
        return True, result
    if type(value) in (list, tuple):
        result = []
        for item in value:
            valid, cloned = _copy_plain_metadata(item, budget, depth + 1)
            if not valid:
                return False, None
            result.append(cloned)
        return True, type(value)(result)
    return False, None


def _unpack_state(value: Any) -> Any:
    # Same ordered unwrapping as the pinned loader, including nested wrappers.
    for key in ("state_dict", "model_state_dict", "model"):
        if type(value) not in (dict, OrderedDict):
            return None
        if key in value:
            value = value[key]
    return value if type(value) in (dict, OrderedDict) else None


def _optimized_load(path: Any, model: Any, ignore_init_mismatch: bool, scope_map: Any,
                    excludes: Any, torch: Any) -> _LoadResult:
    """Return fallback reasons only before loading writes any model parameter.

    When a checkpoint is unsupported, this frame releases its decoded tensors
    before the caller invokes stock loading. Exceptions from real I/O or strict
    shape loading propagate, as they do in the original loader.
    """
    dst_state = model.state_dict()
    if type(dst_state) not in (dict, OrderedDict):
        return _LoadResult("unsupported_destination")
    if any(type(key) is not str or not _plain_cpu_tensor(value, torch)
           for key, value in dst_state.items()):
        return _LoadResult("unsupported_destination")
    metadata = getattr(dst_state, "_metadata", None)
    if metadata is not None:
        valid, metadata = _copy_plain_metadata(metadata, [4096])
        if not valid or type(metadata) not in (dict, OrderedDict) or any(
            type(item) not in (dict, OrderedDict) or item.get("assign_to_params_buffers", False)
            for item in metadata.values()
        ):
            return _LoadResult("unsupported_metadata")
        dst_state._metadata = metadata

    checkpoint = torch.load(path, map_location="cpu")
    src_state = _unpack_state(checkpoint)
    if src_state is None or any(type(key) is not str or not _plain_cpu_tensor(value, torch)
                                for key, value in src_state.items()):
        return _LoadResult("unsupported_checkpoint")
    destination_storages = {value.untyped_storage()._cdata for value in dst_state.values()}
    source_storages = {}
    for value in src_state.values():
        storage = value.untyped_storage()
        if storage._cdata in destination_storages:
            return _LoadResult("source_destination_alias")
        source_storages[storage._cdata] = storage.nbytes()

    # Preserve the pinned loader's scope_map list behavior and its precedence.
    if isinstance(scope_map, str):
        scope_map = scope_map.split(",")
    scope_map += ["module.", "None"]
    if isinstance(excludes, str):
        excludes = excludes.split(",")
    matched_keys = missing_keys = mismatched_keys = excluded_keys = 0
    for key in dst_state:
        if excludes is not None and any(key.startswith(item) for item in excludes):
            excluded_keys += 1
            continue
        source_key = key
        for index in range(0, len(scope_map), 2):
            source_prefix = scope_map[index] if scope_map[index].lower() != "none" else ""
            destination_prefix = scope_map[index + 1] if scope_map[index + 1].lower() != "none" else ""
            if destination_prefix == "" and source_prefix + key in src_state:
                source_key = source_prefix + key
            elif key.startswith(destination_prefix) and key.replace(destination_prefix, source_prefix, 1) in src_state:
                source_key = key.replace(destination_prefix, source_prefix, 1)
        if source_key in src_state:
            if not ignore_init_mismatch or dst_state[key].shape == src_state[source_key].shape:
                dst_state[key] = src_state[source_key]
                matched_keys += 1
            else:
                mismatched_keys += 1
        else:
            missing_keys += 1
    # No assign, swap, precision conversion, tensor subclass, or mmap policy.
    model.load_state_dict(dst_state, strict=True)
    return _LoadResult("ordinary_strict_copy", len(src_state), sum(source_storages.values()),
                       matched_keys, missing_keys, mismatched_keys, excluded_keys)


def _policy_rejection(model: Any, map_location: Any, oss_bucket: Any, scope_map: Any,
                      excludes: Any, kwargs: dict[str, Any], torch: Any) -> str | None:
    if not _is_known_model(model):
        return "unrecognized_model"
    if type(map_location) is not str or map_location != "cpu" or oss_bucket is not None or kwargs:
        return "unsupported_policy"
    if type(scope_map) is str:
        scopes = scope_map.split(",")
    elif type(scope_map) is list:
        scopes = scope_map
    else:
        return "unsupported_policy"
    if len(scopes) % 2 or any(type(item) is not str for item in scopes):
        return "unsupported_policy"
    if excludes is not None and type(excludes) not in (str, list, tuple):
        return "unsupported_policy"
    if type(excludes) in (list, tuple) and any(type(item) is not str for item in excludes):
        return "unsupported_policy"
    if not _safe_module_tree(model, torch):
        return "unsafe_module_tree"
    return None


def install_funasr_cpu_checkpoint_loader(auto_model_class: Any) -> LoaderInstallResult:
    """Replace only AutoModel's pinned function binding in this worker process.

    Fake/test providers and unavailable or changed dependencies remain untouched.
    The original train_utils function is never replaced. Each model call also
    checks its own compatibility and falls back before any optimized model write.
    """
    if getattr(auto_model_class, "__module__", "") != "funasr.auto.auto_model":
        result = LoaderInstallResult(False, "unsupported_provider")
        _emit("stock", result.code)
        return result
    try:
        if importlib.metadata.version("funasr") != FUNASR_VERSION:
            result = LoaderInstallResult(False, "unsupported_funasr_version")
            _emit("stock", result.code)
            return result
        torch = importlib.import_module("torch")
        if str(torch.__version__) != TORCH_VERSION:
            result = LoaderInstallResult(False, "unsupported_torch_version")
            _emit("stock", result.code)
            return result
        auto = importlib.import_module("funasr.auto.auto_model")
        upstream = importlib.import_module("funasr.train_utils.load_pretrained_model")
        original = upstream.load_pretrained_model
        original_path = Path(original.__code__.co_filename)
        if hashlib.sha256(original_path.read_bytes()).hexdigest() != UPSTREAM_LOADER_SHA256:
            result = LoaderInstallResult(False, "unsupported_loader_hash")
        elif auto.AutoModel is not auto_model_class:
            result = LoaderInstallResult(False, "unsupported_provider")
        elif getattr(auto.load_pretrained_model, _MARKER, None) is original:
            result = LoaderInstallResult(True, "already_installed")
        elif auto.load_pretrained_model is not original:
            result = LoaderInstallResult(False, "changed_loader_binding")
        else:
            def controlled_loader(path: Any, model: Any, ignore_init_mismatch: bool = True,
                                  map_location: Any = "cpu", oss_bucket: Any = None,
                                  scope_map: Any = [], excludes: Any = None, **kwargs: Any) -> Any:
                reason = _policy_rejection(model, map_location, oss_bucket, scope_map, excludes, kwargs, torch)
                if reason is None:
                    loaded = _optimized_load(path, model, ignore_init_mismatch, scope_map, excludes, torch)
                    if loaded.code == "ordinary_strict_copy":
                        _emit("optimized", loaded.code, model=type(model).__name__, result=loaded)
                        return None  # The pinned FunASR loader also returns None.
                    reason = loaded.code
                label = type(model).__name__ if _is_known_model(model) else "unsupported_model"
                _emit("stock", reason, model=label)
                return original(path, model, ignore_init_mismatch=ignore_init_mismatch, map_location=map_location,
                                oss_bucket=oss_bucket, scope_map=scope_map, excludes=excludes, **kwargs)

            setattr(controlled_loader, _MARKER, original)
            controlled_loader.__wrapped__ = original
            auto.load_pretrained_model = controlled_loader
            result = LoaderInstallResult(True, "installed")
    except (ImportError, OSError, AttributeError, importlib.metadata.PackageNotFoundError):
        result = LoaderInstallResult(False, "dependency_unavailable")
    _emit("installed" if result.installed else "stock", result.code)
    return result
