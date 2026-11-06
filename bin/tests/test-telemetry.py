import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('telemetry', Path(__file__).resolve().parents[2] / 'tools/telemetry.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TelemetryTests(unittest.TestCase):
    def test_latency_retains_spikes_and_loss(self):
        row = module.latency_summary([10, 10, 10, 100, None], method='protocol-rtt', source='host')
        self.assertEqual(row['average'], 32.5)
        self.assertEqual(row['median'], 10)
        self.assertEqual(row['p95'], 100)
        self.assertEqual(row['loss_pct'], 20)

    def test_missing_data_is_not_zero_latency(self):
        row = module.latency_summary([], method='protocol-rtt', source='host')
        self.assertEqual(row['status'], 'NO_DATA')
        self.assertIsNone(row['average'])
        self.assertIsNone(row['loss_pct'])

    def test_ssh_duration_not_accepted_as_rtt(self):
        with self.assertRaises(ValueError):
            module.latency_summary([10], method='ssh-duration', source='host')

    def test_solo_continuation_fails_even_after_reconnect(self):
        result = module.connection_verdict([
            {'time_ns': 1, 'state': 'connected'},
            {'time_ns': 22, 'state': 'disconnected'},
            {'time_ns': 23, 'state': 'connected'}], 10, 100)
        self.assertEqual(result['status'], 'FAIL')

    def test_unknown_baseline_cannot_pass(self):
        self.assertEqual(module.connection_verdict([], 10, 100)['status'], 'NO_DATA')


if __name__ == '__main__':
    unittest.main()
