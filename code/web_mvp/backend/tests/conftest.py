"""Backend test isolation for process-wide runtime controllers."""

import pytest

from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller
from meeting_copilot_web_mvp import llm_service


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
