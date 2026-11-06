import importlib.util
from pathlib import Path
import tempfile
import types
import unittest

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('protocol', root / 'tools/devbridge.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StateTests(unittest.TestCase):
    def runner(self, text):
        runner = object.__new__(module.Runner)
        runner.protocol = lambda *a: types.SimpleNamespace(stdout=text)
        runner.record = lambda *a, **kw: None
        return runner

    def test_unknown_field_cannot_pass(self):
        runner = self.runner('foreground=game\n')
        with self.assertRaises(module.ProtocolError):
            runner.assert_state([types.SimpleNamespace(alias='host')], ['peer=connected'])

    def test_each_device_must_match_in_same_sample_round(self):
        runner = self.runner('peer=connected\n')
        runner.protocol = lambda d, *a: types.SimpleNamespace(
            stdout='peer=connected\n' if d.alias == 'host' else 'peer=disconnected\n')
        with self.assertRaises(module.ProtocolError):
            runner.assert_state([types.SimpleNamespace(alias=n) for n in ('host', 'guest')],
                                ['peer=connected'])

    def test_matching_fields_pass(self):
        self.runner('foreground=game\npeer=connected\n').assert_state(
            [types.SimpleNamespace(alias='host')], ['foreground=game', 'peer=connected'])

    def test_empty_or_duplicate_predicates_rejected(self):
        for predicates in ([], ['peer=a', 'peer=b'], ['peer']):
            with self.assertRaises(module.ProtocolError):
                self.runner('').assert_state([], predicates)


if __name__ == '__main__':
    unittest.main()
