import http.client
import json
from types import SimpleNamespace

import pytest

from tools import mp3_file_import_acceptance as tool


def test_official_multipart_once_uses_loopback_and_keeps_credentials_in_memory(tmp_path, monkeypatch):
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"existing audio bytes")
    calls = []
    closed = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert host == "127.0.0.1" and port == 8769 and timeout == 30

        def request(self, method, path, **kwargs):
            assert method == "POST" and path == "/v2/meetings/import-audio"
            calls.append(kwargs)

        def getresponse(self):
            return SimpleNamespace(status=202, read=lambda _limit: b'{"meeting_id":"import_test"}')

        def close(self):
            closed.append(True)

    monkeypatch.setattr(tool.http.client, "HTTPConnection", Connection)
    client = SimpleNamespace(origin="http://127.0.0.1:8769", token="PRIVATE_TOKEN", cookie="PRIVATE_COOKIE")
    status, response = tool.post_once(client, audio)
    assert status == 202 and response == {"meeting_id": "import_test"}
    assert len(calls) == len(closed) == 1
    assert calls[0]["headers"]["Origin"] == client.origin
    assert calls[0]["headers"]["X-Meeting-Copilot-Verification"] == "1"
    assert b"existing audio bytes" in calls[0]["body"]
    assert b'name="file"' in calls[0]["body"] and b"audio/mpeg" in calls[0]["body"]
    assert "PRIVATE" not in json.dumps(response)


def test_unknown_upload_response_never_implicitly_retries(tmp_path, monkeypatch):
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"existing audio bytes")
    attempts = []
    closed = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            attempts.append(True)
            raise http.client.RemoteDisconnected("private transport details")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(tool.http.client, "HTTPConnection", Connection)
    client = SimpleNamespace(origin="http://127.0.0.1:8769", token="", cookie=None)
    with pytest.raises(http.client.RemoteDisconnected):
        tool.post_once(client, audio)
    assert len(attempts) == len(closed) == 1
