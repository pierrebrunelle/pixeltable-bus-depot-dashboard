"""UDFs and UDAs for the bus depot dashboard (pure Python, no table imports)."""

from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

import pixeltable as pxt

ON_TIME_MINUTES = 5.0  # a trip is on time if it reaches its last stop at most 5 min late
DEPOT_TZ = ZoneInfo('America/Chicago')  # all three depots run on the same local clock


@pxt.udf
def last_value(values: pxt.Json) -> float | None:
    """Delay at the final stop of a trip (the last element of the per-stop delay list)."""
    if not values:
        return None
    return float(values[-1])


@pxt.udf
def local_hour(ts: dt.datetime) -> int:
    """Hour of day on the depot clock, independent of the session time zone (local machine vs. UTC in the cloud)."""
    return ts.astimezone(DEPOT_TZ).hour


@pxt.udf
def delay_band(delay_min: float | None) -> str:
    """Bucket a delay into the bands the depot managers use on the wall board."""
    if delay_min is None:
        return 'cancelled'
    if delay_min <= 1.0:
        return 'early/on time'
    if delay_min <= ON_TIME_MINUTES:
        return '1-5 min'
    if delay_min <= 15.0:
        return '5-15 min'
    return '15+ min'


@pxt.udf
def parse_telemetry(line: str) -> pxt.Json:
    """Parse one CAN-bus summary line from the bus logger.

    Accepts both logger firmware formats: 'soc=81;temp=34.5;faults=P0420' and the newer
    'soc:81 temp:34.5 faults:P0420'. Returns {'soc': int | None, 'temp_c': float, 'faults': [str, ...]}.
    """
    line = line.strip()
    if '=' in line:
        parts = [p.split('=', 1) for p in line.split(';') if p]
    else:
        parts = [p.split(':', 1) for p in line.split() if p]
    fields = {k.strip(): v.strip() for k, v in parts}
    faults = [f for f in fields.get('faults', '').split(',') if f]
    soc = fields.get('soc')
    return {
        'soc': int(soc) if soc not in (None, '') else None,
        'temp_c': float(fields['temp']),
        'faults': faults,
    }


@pxt.uda
class percentile(pxt.Aggregator):
    """Nearest-rank percentile of a numeric column, e.g. percentile(delay, 0.9) for the p90 delay."""

    def __init__(self, q: float = 0.9):
        self.q = q
        self.values: list[float] = []

    def update(self, val: float | None) -> None:
        if val is not None:
            self.values.append(val)

    def value(self) -> float | None:
        if not self.values:
            return None
        vals = sorted(self.values)
        rank = max(1, math.ceil(self.q * len(vals)))
        return vals[rank - 1]


@pxt.uda
class on_time_rate(pxt.Aggregator):
    """Share of trips (0..1) that reached the last stop at most ON_TIME_MINUTES late; cancelled trips count as late."""

    def __init__(self):
        self.on_time = 0
        self.total = 0

    def update(self, delay_min: float | None) -> None:
        self.total += 1
        if delay_min is not None and delay_min <= ON_TIME_MINUTES:
            self.on_time += 1

    def value(self) -> float | None:
        return self.on_time / self.total if self.total else None
