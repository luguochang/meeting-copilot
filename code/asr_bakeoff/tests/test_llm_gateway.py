import json
from pathlib import Path

from asr_bakeoff.llm_gateway import LlmGatewayConfig, load_llm_gateway_config


def test_load_llm_gateway_config_masks_api_key(tmp_path: Path):
    config_path = tmp_path / "llm-gateway.local.json"
    config_path.write_text(
        json.dumps(
            {
                "base_url": "https://example.test",
                "api_key": "test-key-1234567890abcdef",
                "model": "gpt-5.5",
            }
        ),
        encoding="utf-8",
    )

    config = load_llm_gateway_config(config_path)

    assert config.base_url == "https://example.test"
    assert config.model == "gpt-5.5"
    assert config.masked_api_key == "tes...cdef"


def test_llm_gateway_config_normalizes_base_url():
    config = LlmGatewayConfig(
        base_url="https://example.test/",
        api_key="test-key-abcdef",
        model="gpt-5.5",
    )

    assert config.chat_completions_url == "https://example.test/v1/chat/completions"


def test_llm_gateway_config_uses_certifi_when_available():
    config = LlmGatewayConfig(
        base_url="https://example.test",
        api_key="test-key-abcdef",
        model="gpt-5.5",
        ca_bundle_path="/tmp/cacert.pem",
    )

    assert config.ca_bundle_path == "/tmp/cacert.pem"
