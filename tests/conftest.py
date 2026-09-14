"""Test setup: make the repo root importable as package `hermes_voicy_test`.

The loader imports the plugin as a directory module (ADR-0004); in tests we
mimic that with importlib so relative imports behave identically.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_NAME = "hermes_voicy_test"


def _load_plugin_module():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    init = ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        init,
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = MODULE_NAME
    module.__path__ = [str(ROOT)]
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin_module()
synth = importlib.import_module(f"{MODULE_NAME}.synth")
config = importlib.import_module(f"{MODULE_NAME}.config")
player = importlib.import_module(f"{MODULE_NAME}.player")
hooks = importlib.import_module(f"{MODULE_NAME}.hooks")
presets = importlib.import_module(f"{MODULE_NAME}.presets")
tray = importlib.import_module(f"{MODULE_NAME}.tray")


class FakePlayer:
    """Shared test double: records play_tone() calls synchronously."""

    def __init__(self):
        self.played = []
        self.timeouts = []

    def play_tone(self, wav, backend="auto", bell_allowed=True,
                  timeout=player.PLAY_TIMEOUT):
        self.played.append(str(wav))
        self.timeouts.append(timeout)

    def resolve(self, preference="auto"):
        return "paplay"
