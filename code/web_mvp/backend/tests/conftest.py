"""Backend test isolation for process-wide runtime controllers."""

import pytest

from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller


@pytest.fixture(autouse=True)
def reset_process_wide_degradation_controller():
    controller = get_degradation_controller()
    controller.reset()
    yield
    controller.reset()
