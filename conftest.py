"""pytest bootstrap for a repo whose root IS the plugin package.

The Hermes loader imports ``./__init__.py`` via ``spec_from_file_location``
(ADR-0004). pytest, seeing ``__init__.py`` at the repo root, would wrap the
root directory as a ``Package`` node and import that same file as a test
module — which fails on the plugin's relative imports. We no-op
``Package.setup`` for the root directory only; everything else is untouched.
"""
import os

import _pytest.python


def pytest_configure(config):
    root = str(config.rootpath)
    orig = _pytest.python.Package.setup

    def setup(self):
        try:
            if self.path is not None and os.path.samefile(self.path, root):
                return  # repo root == plugin package: not a test package
        except (OSError, TypeError):
            pass
        return orig(self)

    _pytest.python.Package.setup = setup
