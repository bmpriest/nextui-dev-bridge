#!/usr/bin/env python3
"""PC-side suite lifecycle. Device actions use DevBridge .test files.

Usage: broker.py MANIFEST --out DIR [--resume]
Manifests contain devices and cells. Each cell names preflight, setup, test,
judge, collect, and teardown scripts. Collect and teardown run after failures.
No monitoring process is started during gameplay by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import uuid


STAGES = ('preflight', 'setup', 'test', 'collect', 'judge', 'teardown')


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


class Broker:
    def __init__(self, manifest, out, resume=False, execute=None):
        self.source = Path(manifest).resolve()
        self.spec = json.loads(self.source.read_text())
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.execute = execute or self.execute_script
        self.results = {}
        self.leases = []
        self.quarantined = set()
        self.validate()
        metadata = self.out / 'suite.json'
        if metadata.exists():
            previous = json.loads(metadata.read_text())
            if not resume:
                raise ValueError('output already contains a suite; use --resume')
            if previous['manifest_sha256'] != self.digest:
                raise ValueError('manifest changed; start a new output directory')
        else:
            atomic_json(metadata, {'manifest_sha256': self.digest,
                                  'manifest': self.spec})

    def validate(self):
        ids = set()
        for cell in self.spec['cells']:
            name = cell['id']
            if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in name):
                raise ValueError(f'invalid cell ID: {name}')
            if name in ids:
                raise ValueError(f'duplicate cell ID: {name}')
            ids.add(name)
            for alias in cell.get('devices', []):
                if alias not in self.spec['devices']:
                    raise ValueError(f'unknown device: {alias}')
            if not cell.get('unsupported'):
                for stage in STAGES:
                    if not cell.get(stage):
                        raise ValueError(f'{name}: missing {stage} script')
                    value = cell[stage]
                    if isinstance(value, str):
                        script = (self.source.parent / value).resolve()
                        if not script.is_file():
                            raise ValueError(f'{name}: missing script {script}')
                    elif not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
                        raise ValueError(f'{name}: {stage} must be a script path or argv list')
                if cell.get('timeout_s', 1200) <= 0:
                    raise ValueError('timeout_s must be positive')

    def event(self, path, kind, **fields):
        with (path / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps({'event': kind, 'time_ns': time.time_ns(),
                                     'monotonic_ns': time.monotonic_ns(), **fields}) + '\n')

    def execute_script(self, cell, stage, path):
        value = cell[stage]
        if isinstance(value, list):
            if not value or not all(isinstance(x, str) for x in value):
                raise ValueError(f'{cell["id"]}: {stage} command must be a nonempty argv list')
            command = [x.replace('{out}', str(path)) for x in value]
            with (path / f'{stage}.log').open('w') as log:
                process = subprocess.Popen(command, cwd=self.source.parent, stdout=log,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           env={**os.environ, 'DEVBRIDGE_ATTEMPT_DIR': str(path)},
                                           start_new_session=True)
                try:
                    return process.wait(timeout=cell.get('timeout_s', 1200))
                except BaseException:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
        script = (self.source.parent / value).resolve()
        argv = [sys.executable, '-P', str(Path(__file__).with_name('devbridge.py'))]
        for alias in cell['devices']:
            device = self.spec['devices'][alias]
            if 'adb' in device:
                argv += ['--adb', f"{device['adb']}={alias}"]
            else:
                argv += ['--connect', f"{device['address']}:{alias}:{device.get('port', 22)}"]
        argv += self.spec.get('devbridge_args', [])
        argv += ['--session-root', str(path / stage), 'run', str(script)]
        (path / f'{stage}.test').write_bytes(script.read_bytes())
        environment = os.environ.copy()
        environment['DEVBRIDGE_ATTEMPT_DIR'] = str(path)
        with (path / f'{stage}.log').open('w') as log:
            try:
                process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, env=environment,
                                           start_new_session=True)
                try:
                    return process.wait(timeout=cell.get('timeout_s', 1200))
                except BaseException:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
            except subprocess.TimeoutExpired:
                return 124

    def acquire(self):
        # Atomic directory leases shared across broker output directories.
        root = Path(os.environ.get('DEVBRIDGE_LEASE_DIR', '/tmp/devbridge-device-leases'))
        root.mkdir(parents=True, exist_ok=True)
        for alias, device in sorted(self.spec['devices'].items()):
            identity = device.get('id') or device.get('adb') or device.get('address')
            if not identity:
                raise ValueError(f'{alias}: missing stable device identity')
            lease = root / hashlib.sha256(identity.encode()).hexdigest()
            try:
                lease.mkdir()
            except FileExistsError:
                raise RuntimeError(f'{alias}: device leased; inspect {lease}/owner.json')
            self.leases.append(lease)
            atomic_json(lease / 'owner.json', {'pid': os.getpid(), 'out': str(self.out),
                                               'device': alias})

    def release(self):
        for lease in reversed(self.leases):
            if lease in self.quarantined:
                continue
            (lease / 'owner.json').unlink()
            lease.rmdir()
        self.leases.clear()

    def run_cell(self, cell):
        directory = self.out / cell['id']
        directory.mkdir(exist_ok=True)
        attempt = directory / ('attempt-' + uuid.uuid4().hex[:12])
        attempt.mkdir()
        result = {'cell': cell['id'], 'verdict': 'NOT_RUN', 'stages': {},
                  'artifacts': str(attempt.relative_to(self.out))}
        atomic_json(attempt / 'manifest.json', cell)
        primary = None
        try:
            for stage in STAGES:
                if primary and stage not in ('collect', 'teardown'):
                    result['stages'][stage] = {'state': 'NOT_RUN'}
                    continue
                self.event(attempt, 'stage_start', stage=stage)
                started = time.monotonic()
                # Persist before executing so an interrupted attempt is visible.
                result['stages'][stage] = {'state': 'RUNNING'}
                atomic_json(attempt / 'result.json', result)
                try:
                    rc = self.execute(cell, stage, attempt)
                except (Exception, KeyboardInterrupt) as error:
                    rc = 125
                    if isinstance(error, KeyboardInterrupt):
                        result['interrupted'] = True
                    self.event(attempt, 'exception', stage=stage, message=str(error))
                result['stages'][stage] = {'state': 'PASS' if rc == 0 else 'FAIL',
                                           'returncode': rc,
                                           'duration_s': time.monotonic() - started}
                self.event(attempt, 'stage_end', stage=stage, returncode=rc)
                if rc and primary is None:
                    primary = stage
                    result['failure_stage'] = stage
                    result['verdict'] = ('PRODUCT_FAIL' if stage in ('test', 'judge')
                                         else 'INFRA_FAIL')
            if primary is None:
                result['verdict'] = 'PASS'
            result['restore_failed'] = result['stages']['teardown']['state'] != 'PASS'
            if result['restore_failed']:
                result['quarantined_devices'] = list(cell.get('devices', []))
                self.quarantined.update(lease for lease in self.leases)
        finally:
            atomic_json(attempt / 'result.json', result)
        return result

    def report(self):
        rows = []
        for cell in self.spec['cells']:
            result = self.results.get(cell['id'], {'verdict': 'NOT_RUN'})
            rows.append({'cell': cell['id'], **result})
        atomic_json(self.out / 'results.json', rows)
        lines = ['| Cell | Result | Evidence |', '|---|---|---|']
        for row in rows:
            artifact = row.get('artifacts')
            link = f"[attempt]({artifact}/result.json)" if artifact else row.get('reason', '')
            lines.append(f"| {row['cell']} | {row['verdict']} | {link} |")
        (self.out / 'report.md').write_text('\n'.join(lines) + '\n')

    def run(self):
        self.report()
        try:
            self.acquire()
            for cell in self.spec['cells']:
                if cell.get('unsupported'):
                    self.results[cell['id']] = {'verdict': 'UNSUPPORTED', 'reason': cell['unsupported']}
                else:
                    previous = sorted((self.out / cell['id']).glob('attempt-*/result.json'),
                                      key=lambda p: p.stat().st_mtime_ns)
                    latest = json.loads(previous[-1].read_text()) if previous else None
                    if self.resume and latest and latest['verdict'] == 'PASS':
                        self.results[cell['id']] = latest
                        continue
                    self.results[cell['id']] = self.run_cell(cell)
                self.report()
                if self.results[cell['id']].get('restore_failed') or self.results[cell['id']].get('interrupted'):
                    break
        finally:
            self.report()
            self.release()
        return 0 if all(r['verdict'] in ('PASS', 'UNSUPPORTED') for r in self.results.values()) and len(self.results) == len(self.spec['cells']) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--out', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    return Broker(args.manifest, args.out, args.resume).run()


if __name__ == '__main__':
    sys.exit(main())
