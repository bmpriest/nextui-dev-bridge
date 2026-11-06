"""Offline telemetry calculations; this module performs no device I/O."""
import math
import statistics


def latency_summary(samples, *, method, source, attempted=None):
    """Summarize RTT samples in milliseconds from one measurement source.

    A missing reply is None. Callers must slice samples to the named gameplay
    interval first. Protocol RTT with unknown request count cannot report loss.
    """
    if method not in ('protocol-rtt', 'probe-rtt'):
        raise ValueError('latency method must identify round-trip measurement')
    if not source:
        raise ValueError('latency source is required')
    values = []
    missing = 0
    for sample in samples:
        if sample is None:
            missing += 1
        elif isinstance(sample, bool) or not isinstance(sample, (int, float)) or not math.isfinite(sample) or sample < 0:
            raise ValueError('RTT must be a finite nonnegative millisecond value')
        else:
            values.append(sample)
    if attempted is not None and (attempted < len(values) + missing or attempted <= 0):
        raise ValueError('invalid attempted request count')
    count = attempted if attempted is not None else (len(values) + missing if missing else None)
    ordered = sorted(values)
    result = dict(method=method, source=source, unit='ms', sample_count=len(values),
                  status='OK' if values else 'NO_DATA',
                  loss_pct=100 * (count - len(values)) / count if count else None,
                  average=None, median=None, p95=None, samples=list(samples))
    if values:
        result.update(average=statistics.mean(values), median=statistics.median(values),
                      p95=ordered[math.ceil(.95 * len(ordered)) - 1])
    return result


def connection_verdict(events, start_ns, end_ns):
    """Require a known connected state at start and no disconnect in the window.

    Events are ordered {time_ns, state}; callers supply process/session scoped
    events. A missing baseline is inconclusive, never a successful connection.
    """
    if end_ns <= start_ns:
        raise ValueError('measurement interval must be positive')
    state = None
    for event in sorted(events, key=lambda e: e['time_ns']):
        if event['state'] not in ('connected', 'disconnected', 'exited'):
            raise ValueError('unknown connection state')
        if event['time_ns'] <= start_ns:
            state = event['state']
        elif event['time_ns'] <= end_ns and event['state'] != 'connected':
            return {'status': 'FAIL', 'reason': 'connection lost during measurement',
                    'event': event}
    if state != 'connected':
        return {'status': 'NO_DATA' if state is None else 'FAIL',
                'reason': 'peer not known connected at measurement start'}
    return {'status': 'PASS'}
