"""Run the fixture tests with the Python standard-library test runner."""

import tempfile
import unittest
from pathlib import Path

import test_lint
import test_hook
import test_hook_log
import test_codex_hook
import test_quote
import test_session_lookup
import test_provenance_hook
import test_verify


class FixtureTests(unittest.TestCase):
    __test__ = False  # pytest runs the source tests directly.


def _wrap(test_function):
    def run_test(self):
        with tempfile.TemporaryDirectory() as directory:
            test_function(Path(directory))
    return run_test


for module in (test_verify, test_lint, test_hook, test_hook_log, test_codex_hook, test_quote, test_provenance_hook, test_session_lookup):
    for name in dir(module):
        if name.startswith("test_") and callable(getattr(module, name)):
            setattr(FixtureTests, name, _wrap(getattr(module, name)))
