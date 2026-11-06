import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

module_path = Path(__file__).with_name('broker.py')
if not module_path.exists():
    module_path = Path(__file__).resolve().parents[2] / 'tools/broker.py'
spec = importlib.util.spec_from_file_location('broker', module_path)
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class LifecycleTests(unittest.TestCase):
    def suite(self, root, execute):
        root = Path(root)
        (root / 'action.test').write_text('STATUS\n')
        cells = [dict(id=name, devices=['host'], **{s: 'action.test' for s in broker.STAGES})
                 for name in ('first', 'second')]
        manifest = root / 'suite.json'
        manifest.write_text(json.dumps({'devices': {'host': {'adb': 'serial'}}, 'cells': cells}))
        return broker.Broker(manifest, root / 'out', execute=execute)

    def test_failed_launch_skips_play_and_always_collects_and_restores(self):
        with tempfile.TemporaryDirectory() as root:
            calls = []
            def execute(cell, stage, path):
                calls.append(stage)
                return 1 if stage == 'setup' else 0
            run = self.suite(root, execute)
            result = run.run_cell(run.spec['cells'][0])
            self.assertEqual(calls, ['preflight', 'setup', 'collect', 'teardown'])
            self.assertEqual(result['verdict'], 'INFRA_FAIL')
            self.assertEqual(result['stages']['test']['state'], 'NOT_RUN')

    def test_interrupt_restores_devices(self):
        with tempfile.TemporaryDirectory() as root:
            calls = []
            def execute(cell, stage, path):
                calls.append(stage)
                if stage == 'test':
                    raise KeyboardInterrupt()
                return 0
            run = self.suite(root, execute)
            result = run.run_cell(run.spec['cells'][0])
            self.assertTrue(result['interrupted'])
            self.assertEqual(calls[-2:], ['collect', 'teardown'])

    def test_failed_restore_stops_suite_and_quarantines_devices(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'DEVBRIDGE_LEASE_DIR': root + '/leases'}):
            run = self.suite(root, lambda c, s, p: 1 if s == 'teardown' else 0)
            self.assertEqual(run.run(), 1)
            rows = json.loads((run.out / 'results.json').read_text())
            self.assertEqual([r['verdict'] for r in rows], ['INFRA_FAIL', 'NOT_RUN'])
            self.assertEqual(len(list((Path(root) / 'leases').iterdir())), 1)

    def test_resume_skips_completed_cells(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'DEVBRIDGE_LEASE_DIR': root + '/leases'}):
            run = self.suite(root, lambda c, s, p: 0)
            self.assertEqual(run.run(), 0)
            def unexpected(*args):
                self.fail('completed cell was rerun')
            resumed = broker.Broker(run.source, run.out, resume=True, execute=unexpected)
            self.assertEqual(resumed.run(), 0)

    def test_lease_prevents_concurrent_control(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'DEVBRIDGE_LEASE_DIR': root + '/leases'}):
            run = self.suite(root, lambda c, s, p: 0)
            run.acquire()
            try:
                other = broker.Broker(run.source, Path(root) / 'other', execute=lambda *a: 0)
                with self.assertRaises(RuntimeError):
                    other.acquire()
            finally:
                run.release()

    def test_local_fixture_commands_return_exit_status(self):
        with tempfile.TemporaryDirectory() as root:
            run = self.suite(root, lambda *a: 0)
            cell = run.spec['cells'][0]
            cell['setup'] = ['sh', '-c', 'exit 7']
            result = run.execute_script(cell, 'setup', Path(root))
            self.assertEqual(result, 7)


if __name__ == '__main__':
    unittest.main()
