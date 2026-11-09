"""The package surface: what other modules and the console script may import."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vision_system.apps import server_gui

# The names that existed before the panel was split into a package. Anything that
# disappears from here is a breaking change for an importer, not an internal move.
PUBLIC_SURFACE = (
    "PRESENCE_TIMEOUT_NS",
    "STALE_POSE_NS",
    "VIEWPORT_QUANTUM_M",
    "DeploymentStatus",
    "ServerLaunchOptions",
    "ServerProcess",
    "StatusMonitor",
    "WorldModel",
    "build_server_command",
    "build_server_environment",
    "fit_viewport",
    "roster_from_config",
    "server_gui_main",
)


@pytest.mark.parametrize("name", PUBLIC_SURFACE)
def test_the_package_still_exposes_its_public_names(name):
    assert hasattr(server_gui, name)
    assert name in server_gui.__all__


@pytest.mark.parametrize("module", ["vision_system.apps.server_gui", "vision_system.gui"])
def test_importing_the_panel_does_not_pull_in_tkinter(module):
    # The whole suite runs headless only because tkinter is imported lazily, inside
    # the widget classes. A stray module-scope import would break CI on any machine
    # without python3-tk, so assert the contract instead of trusting review.
    probe = f"import {module}, sys; assert 'tkinter' not in sys.modules"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, env=environment
    )
    assert result.returncode == 0, result.stderr
