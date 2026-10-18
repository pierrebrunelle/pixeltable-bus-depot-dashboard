"""Build the depot operations dashboard as output/dashboard.md and output/dashboard.html.

    python dashboard.py                                # local catalog 'depots'
    DEPOT_CATALOG=pxt://<org>:<db>/depots python dashboard.py # read a Pixeltable Cloud database

Every number comes from a Pixeltable query: group_by aggregates (count, sum, mean, max and
the percentile/on_time_rate UDAs from udfs.py), distinct() lookups, a windowed running total,
the @pxt.query functions from app.py, and a data-health section that lists rows whose
computed telemetry column failed.
"""

from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path

import pixeltable as pxt
import pixeltable.functions as pxtf

from udfs import DEPOT_TZ, on_time_rate, percentile

CATALOG = os.environ.get('DEPOT_CATALOG', 'depots')
OUT = Path(__file__).parent / 'output'


def fmt(v) -> str:
    if v is None:
        return '-'
    if isinstance(v, float):
        return f'{v:.2f}'
    if isinstance(v, dt.datetime):
        return v.astimezone(DEPOT_TZ).strftime('%Y-%m-%d %H:%M %Z')
    return str(v)


def table(title: str, rows: list[dict], pct: tuple[str, ...] = ()) -> tuple[str, str]:
    """Render one section as (markdown, html)."""
    if not rows:
        return f'## {title}\n\n_no rows_\n', f'<h2>{html.escape(title)}</h2><p><em>no rows</em></p>'
    cols = list(rows[0].keys())
    cell = lambda r, c: f'{r[c]:.0%}' if c in pct and r[c] is not None else fmt(r[c])  # noqa: E731
    md = [f'## {title}', '', '| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    md += ['| ' + ' | '.join(cell(r, c) for c in cols) + ' |' for r in rows]
    ht = [f'<h2>{html.escape(title)}</h2><table><tr>' + ''.join(f'<th>{html.escape(c)}</th>' for c in cols) + '</tr>']
    ht += ['<tr>' + ''.join(f'<td>{html.escape(cell(r, c))}</td>' for c in cols) + '</tr>' for r in rows]
    return '\n'.join(md) + '\n', ''.join(ht) + '</table>'


def sections() -> list[tuple[str, list[dict], tuple[str, ...]]]:
    trips = pxt.get_table(f'{CATALOG}/trips')
    buses = pxt.get_table(f'{CATALOG}/buses')
    depots = pxt.get_table(f'{CATALOG}/depots')
    out = []

    scorecard = (
        trips.group_by(trips.depot_id)
        .order_by(trips.depot_id)
        .select(
            trips.depot_id,
            trips=pxtf.count(trips.trip_id),
            passengers=pxtf.sum(trips.passengers),
            on_time=on_time_rate(trips.final_delay),
            mean_delay=pxtf.mean(trips.final_delay),
            p90_delay=percentile(trips.final_delay, q=0.9),
            worst_delay=pxtf.max(trips.final_delay),
        )
        .collect()
    )
    scorecard = [dict(r) for r in scorecard]
    names = {r['depot_id']: r['name'] for r in depots.select(depots.depot_id, depots.name).collect()}
    for r in scorecard:
        r['depot_id'] = f"{r['depot_id']} ({names.get(r['depot_id'], '?')})"
    out.append(('Depot scorecard', scorecard, ('on_time',)))

    routes = []
    for depot_id in sorted(names):
        routes += [{'depot': depot_id, **r} for r in route_scorecard_rows(trips, depot_id)]
    out.append(('Route reliability', routes, ('on_time',)))

    bands = trips.group_by(trips.band).order_by(trips.band).select(trips.band, trips=pxtf.count(trips.trip_id)).collect()
    out.append(('Final-delay bands (all depots)', list(bands), ()))

    peak = (
        trips.where(trips.final_delay != None)  # noqa: E711
        .group_by(trips.hour)
        .order_by(trips.hour)
        .select(trips.hour, trips=pxtf.count(trips.trip_id), mean_delay=pxtf.mean(trips.final_delay),
                p90_delay=percentile(trips.final_delay, q=0.9))
        .collect()
    )
    out.append(('Delay by departure hour', list(peak), ()))

    mix = buses.select(buses.depot_id, buses.fuel).distinct().order_by(buses.depot_id, buses.fuel).collect()
    out.append(('Fleet mix (distinct depot x fuel)', list(mix), ()))
    served = trips.select(trips.depot_id, trips.route).distinct().order_by(trips.depot_id, trips.route).collect()
    out.append(('Routes served (distinct depot x route)', list(served), ()))
    hot = (
        trips.where(trips.final_delay > 5.0)
        .select(trips.route, trips.worst_stop)
        .distinct()
        .order_by(trips.route, trips.worst_stop)
        .collect()
    )
    out.append(('Worst stop on late trips (distinct route x stop)', list(hot), ()))

    running = (
        trips.where(trips.depot_id == 'PW')
        .order_by(trips.route, trips.departure)
        .select(trips.trip_id, trips.departure, trips.passengers,
                running_passengers=pxtf.sum(trips.passengers, group_by=trips.route, order_by=trips.departure),
                route=trips.route)
        .limit(8)
        .collect()
    )
    out.append(('Port West: running passenger total per route (window sum, first 8 rows)', list(running), ()))

    late = list(depot_trips_rows(trips, 'HL', 15.0))
    out.append(('Hillside trips 15+ min late (depot_trips query)', late[:10], ()))

    errs = (
        trips.where(trips.telemetry_parsed.errortype != None)  # noqa: E711
        .group_by(trips.bus_id)
        .order_by(trips.bus_id)
        .select(trips.bus_id, failed_rows=pxtf.count(trips.trip_id), sample=pxtf.max(trips.telemetry))
        .collect()
    )
    out.append(('Data health: telemetry rows that failed to parse', list(errs), ()))
    return out


def route_scorecard_rows(trips, depot_id: str):
    return (
        trips.where(trips.depot_id == depot_id)
        .group_by(trips.route)
        .order_by(trips.route)
        .select(trips.route, trips=pxtf.count(trips.trip_id), on_time=on_time_rate(trips.final_delay),
                mean_delay=pxtf.mean(trips.final_delay), p90_delay=percentile(trips.final_delay, q=0.9))
        .collect()
    )


def depot_trips_rows(trips, depot_id: str, min_delay: float):
    return (
        trips.where((trips.depot_id == depot_id) & (trips.final_delay >= min_delay))
        .order_by(trips.final_delay, asc=False)
        .select(trips.trip_id, trips.route, trips.departure, trips.final_delay, trips.band)
        .collect()
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    md, ht = ['# Bus depot operations dashboard', '', f'Catalog: `{CATALOG}`', ''], []
    for title, rows, pct in sections():
        m, h = table(title, rows, pct)
        md.append(m)
        ht.append(h)
        print(m)
    (OUT / 'dashboard.md').write_text('\n'.join(md))
    style = 'body{font-family:system-ui;margin:2rem}table{border-collapse:collapse;margin-bottom:1.5rem}td,th{border:1px solid #ccc;padding:4px 8px;text-align:right}'
    (OUT / 'dashboard.html').write_text(
        f'<!doctype html><meta charset="utf-8"><title>Bus depot dashboard</title><style>{style}</style>'
        f'<h1>Bus depot operations dashboard</h1>' + ''.join(ht)
    )
    print(f'wrote {OUT / "dashboard.md"} and {OUT / "dashboard.html"}')


if __name__ == '__main__':
    main()
