from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from meeting_copilot_web_mvp.storage_governance import (
    PRIVATE_FILE_MODE,
    ensure_private_directory,
    harden_private_file,
)


PROVIDER_CONFIG_SCHEMA_VERSION = 1
PROVIDER_CONFIG_DIRECTORY = "settings"
PROVIDER_CONFIG_FILENAME = "provider.json"


class ProviderConfigStore:
    """Owner-only local persistence for the Web runtime's LLM credential."""

    def __init__(self, data_dir: str | Path) -> None:
        root = ensure_private_directory(data_dir)
        self._directory = ensure_private_directory(root / PROVIDER_CONFIG_DIRECTORY)
        self._path = self._directory / PROVIDER_CONFIG_FILENAME
        self._lock = RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._path.exists():
                return None
            harden_private_file(self._path)
            if self._path.is_symlink():
                raise ValueError("provider config must not be a symbolic link")
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("provider config is unreadable") from exc
            if not isinstance(payload, dict) or payload.get("schema_version") != PROVIDER_CONFIG_SCHEMA_VERSION:
                raise ValueError("provider config schema is unsupported")
            required = ("base_url", "api_key", "model")
            if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in required):
                raise ValueError("provider config is incomplete")
            return dict(payload)

    def save(self, payload: dict[str, Any]) -> None:
        required = ("base_url", "api_key", "model")
        if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in required):
            raise ValueError("provider config is incomplete")
        serialized = json.dumps(
            {"schema_version": PROVIDER_CONFIG_SCHEMA_VERSION, **payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            temporary = self._directory / f".{self._path.name}.{os.getpid()}.tmp"
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                PRIVATE_FILE_MODE,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(serialized)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                harden_private_file(temporary)
                os.replace(temporary, self._path)
                harden_private_file(self._path)
            except BaseException:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    def clear(self) -> None:
        with self._lock:
            if self._path.is_symlink():
                raise ValueError("provider config must not be a symbolic link")
            self._path.unlink(missing_ok=True)
