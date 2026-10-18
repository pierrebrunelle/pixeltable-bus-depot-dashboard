"""Bus depot operations dashboard built with Pixeltable.

Three depots, their buses and a few weeks of trips. Each trip carries the delay at
every stop as a JSON list; computed columns turn that into the final delay, the late
stops and the worst stops (pixeltable.functions map/filter/sort over JSON arrays), a
delay band, and the parsed CAN-bus telemetry line from the bus logger. The dashboard
(`python dashboard.py`) is a set of group_by aggregates (built-in count/sum/mean/max
plus two custom UDAs, p90 delay and on-time rate), distinct() lookups and a windowed
running total, written to output/dashboard.md and output/dashboard.html.

Layout: UDFs/UDAs live in udfs.py; this file holds table models and @pxt.query functions.
No HTTP router.

    pxt schema update app.py depots
    python seed.py
    python dashboard.py
"""

from __future__ import annotations

import pixeltable as pxt
import pixeltable.functions as pxtf

from udfs import delay_band, last_value, local_hour, on_time_rate, parse_telemetry, percentile

TableModel = pxt.model_base()


class Depots(TableModel, name='depots'):
    """One bus depot (garage) and its capacity."""

    depot_id = pxt.Column(type=pxt.String, primary_key=True)
    name: pxt.String
    city: pxt.String
    bays: pxt.Int


class Buses(TableModel, name='buses'):
    """One bus in the fleet and the depot it is based at."""

    bus_id = pxt.Column(type=pxt.String, primary_key=True)
    depot_id: pxt.String
    model: pxt.String
    fuel: pxt.String
    year: pxt.Int

    age_years = 2026 - year


class Trips(TableModel, name='trips'):
    """One scheduled trip: per-stop delays (JSON list of {stop, delay}) and the logger's telemetry line."""

    trip_id = pxt.Column(type=pxt.String, primary_key=True)
    bus_id: pxt.String
    depot_id: pxt.String
    route: pxt.String
    departure: pxt.Timestamp
    passengers: pxt.Int
    stops: pxt.Json
    telemetry: pxt.String

    stop_delays = pxtf.map(stops, lambda x: x.delay)
    late_stops = pxtf.filter(stops, lambda x: x.delay > 5.0)
    worst_stops = pxtf.sort(stops, key=lambda x: x.delay, asc=False)
    final_delay = last_value(stop_delays)
    worst_stop = worst_stops[0].stop.astype(pxt.String | None)
    band = delay_band(final_delay)
    hour = local_hour(departure)
    telemetry_parsed = parse_telemetry(telemetry)

    __indexes__ = [pxt.BtreeIndex(depot_id), pxt.BtreeIndex(route)]


@pxt.query
def depot_trips(depot_id: str, min_delay: float = 0.0):
    """Trips of one depot whose final delay is at least min_delay minutes, worst first."""
    return (
        Trips.where((Trips.depot_id == depot_id) & (Trips.final_delay >= min_delay))
        .order_by(Trips.final_delay, asc=False)
        .select(Trips.trip_id, Trips.route, Trips.departure, Trips.final_delay, Trips.band)
    )


@pxt.query
def route_scorecard(depot_id: str):
    """Per-route reliability for one depot: trips, on-time rate, mean and p90 final delay."""
    return (
        Trips.where(Trips.depot_id == depot_id)
        .group_by(Trips.route)
        .order_by(Trips.route)
        .select(
            Trips.route,
            trips=pxtf.count(Trips.trip_id),
            on_time=on_time_rate(Trips.final_delay),
            mean_delay=pxtf.mean(Trips.final_delay),
            p90_delay=percentile(Trips.final_delay, q=0.9),
        )
    )


@pxt.query
def bus_faults(bus_id: str):
    """Telemetry history of one bus: battery state of charge, temperature and fault codes per trip."""
    return (
        Trips.where(Trips.bus_id == bus_id)
        .order_by(Trips.departure)
        .select(Trips.trip_id, Trips.departure, Trips.telemetry_parsed)
    )
