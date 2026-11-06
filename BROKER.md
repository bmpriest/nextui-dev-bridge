# DevBridge broker implementation status

Run a suite from the PC:

    python3 tools/broker.py /path/to/suite.json --out /path/to/results
    python3 tools/broker.py /path/to/suite.json --out /path/to/results --resume

The initial broker owns device leases, sequential cell execution, stage deadlines,
failure propagation, collection and teardown after failures, per-attempt journals,
and reports that include cells not run. A failed restoration stops the suite.
The manifest and each rendered stage script are saved with the attempt. Passwords
continue to come from DevBridge's environment configuration.

Each manifest has `devices`, optional `devbridge_args`, and `cells`. Devices name
an `id` and either an `adb` serial or `address`/`port`. A cell names its device
aliases and six `.test` paths relative to the manifest: `preflight`, `setup`,
`test`, `judge`, `collect`, and `teardown`. Each phase runs through the existing
DevBridge test runner with fail-fast behavior. Each phase currently has the
cell's `timeout_s` budget. Explicit `unsupported` cells contain a reason and do
not execute scripts. Results are PASS, PRODUCT_FAIL, INFRA_FAIL, UNSUPPORTED,
or NOT_RUN. All failed attempts remain on disk.

New test commands:

    all:ASSERT_STATE input=ready foreground=game
    all:WAIT_STATE 30000 foreground=game

These compare typed status fields from every selected device. Unknown fields
fail. WAIT_STATE rechecks all devices each round, so a device that ceases to
match while its peer starts cannot satisfy the final round. These checks are
for setup and navigation; do not poll them repeatedly during performance tests.
VERIFY, VERIFY_WAIT, and VERIFY_UNTIL continue to handle screen conditions.

`tools/telemetry.py` performs offline RTT and connection calculations without
device I/O. RTT summaries require a named protocol/probe source and preserve
the samples, mean, median, p95, and loss when the attempt count is known.
Unknown data remains NO_DATA. Connection validation rejects any disconnect
inside the measurement interval, including one followed by a reconnect.

This is an initial lifecycle implementation. The existing matrix's network,
emulator, and restoration fixtures have not yet been migrated; launch request
IDs, agent event cursors, persistent crash recovery, conditional test routing,
and telemetry ingestion remain planned. A process killed with SIGKILL leaves
its device lease deliberately held for inspection. Automatic resume must not
steal a lease or assume device restoration. Do not treat broker smoke tests as
emulation-quality or multiplayer validation. No telemetry profile is approved
for performance claims until the overhead comparison on light and heavy cores
has passed.

Validation:

    python3 bin/tests/test-broker.py
    python3 bin/tests/test-state.py
    python3 bin/tests/test-telemetry.py
    python3 bin/tests/test-protocol.py
