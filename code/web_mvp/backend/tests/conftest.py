"""Backend test isolation for process-wide runtime controllers."""

import os

import pytest

from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller
from meeting_copilot_web_mvp import llm_service


@pytest.fixture(autouse=True)
def reset_process_wide_runtime_environment():
    """Restore test-only configuration even when a helper writes os.environ."""
    prefixes = ("LLM_", "ASR_", "PI_", "MEETING_COPILOT_")
    original_environment = os.environ
    saved = {
        name: value
        for name, value in original_environment.items()
        if name.startswith(prefixes)
    }
    try:
        yield
    finally:
        environments = [original_environment]
        if os.environ is not original_environment:
            environments.append(os.environ)
        for environment in environments:
            for name in tuple(environment):
                if name.startswith(prefixes) and name not in saved:
                    del environment[name]
            environment.update(saved)


@pytest.fixture(autouse=True)
def reset_process_wide_degradation_controller():
    controller = get_degradation_controller()
    controller.reset()
    yield
    controller.reset()


@pytest.fixture(autouse=True)
def reset_process_wide_llm_runtime_config():
    """Prevent one test's desktop provider override from leaking into another."""
    llm_service.clear_runtime_config()
    yield
    llm_service.clear_runtime_config()
