"""Insert a deterministic demo fleet: 3 depots, 18 buses and two weeks of trips.

    pxt schema update app.py depots
    python seed.py                                # local catalog 'depots'
    DEPOT_CATALOG=pxt://<org>:<db>/depots python seed.py # same rows into a Pixeltable Cloud database

Six battery buses at the North depot run newer logger firmware that writes telemetry as
'soc:81 temp:34.5 faults:' instead of 'soc=81;temp=34.5;faults='. The first version of
parse_telemetry() only knows the old format, so those rows show up in `pxt errors`.
"""

from __future__ import annotations

import datetime as dt
import os
import random
from zoneinfo import ZoneInfo

import pixeltable as pxt

CATALOG = os.environ.get('DEPOT_CATALOG', 'depots')
START = dt.datetime(2026, 10, 1, 5, 30, tzinfo=ZoneInfo('America/Chicago'))  # timezone-aware: same instant locally and in the cloud
DAYS = 14

DEPOTS = [
    dict(depot_id='NB', name='North Bank', city='Riverton', bays=40),
    dict(depot_id='HL', name='Hillside', city='Riverton', bays=28),
    dict(depot_id='PW', name='Port West', city='Bayview', bays=35),
]
ROUTES = {
    'NB': {'N1': ['Depot Rd', 'Mill St', 'Market Sq', 'Central Stn'], 'N4': ['Depot Rd', 'Park Ave', 'Hospital', 'University']},
    'HL': {'H2': ['Hillside', 'Quarry Ln', 'Old Town', 'Central Stn'], 'H7': ['Hillside', 'Ridge Rd', 'Airport']},
    'PW': {'P3': ['Port West', 'Docks', 'Ferry Term', 'Market Sq'], 'P9': ['Port West', 'Beach Rd', 'Lighthouse', 'Docks', 'Mall']},
}
MODELS = {'battery': ['Volta E12', 'Arc 40e'], 'diesel': ['Citiliner D', 'Metro 400'], 'cng': ['Metro 400G']}
FUELS = {'NB': ['battery', 'battery', 'battery', 'battery', 'battery', 'battery'], 'HL': ['diesel', 'diesel', 'cng', 'cng', 'diesel', 'battery'], 'PW': ['diesel', 'cng', 'diesel', 'battery', 'diesel', 'diesel']}
# per-route congestion profile: how many minutes of delay each stop adds on average
CONGESTION = {'N1': 1.2, 'N4': 2.0, 'H2': 0.8, 'H7': 3.1, 'P3': 1.6, 'P9': 2.4}


def buses() -> list[dict]:
    rows = []
    for depot, fuels in FUELS.items():
        for i, fuel in enumerate(fuels):
            rows.append(dict(bus_id=f'{depot}-{101 + i}', depot_id=depot, model=MODELS[fuel][i % len(MODELS[fuel])],
                             fuel=fuel, year=2014 + (i * 3 + len(depot)) % 12))
    return rows


def telemetry(rng: random.Random, bus: dict) -> str:
    soc = rng.randint(35, 98) if bus['fuel'] == 'battery' else ''
    temp = round(rng.uniform(24, 41), 1)
    faults = ','.join(rng.sample(['P0420', 'U0100', 'B1318', 'C0035'], k=1)) if rng.random() < 0.12 else ''
    if bus['depot_id'] == 'NB' and int(bus['bus_id'][-1]) % 2 == 0:  # new logger firmware on NB-102/104/106
        return f'soc:{soc} temp:{temp} faults:{faults}'
    return f'soc={soc};temp={temp};faults={faults}'


def trips(fleet: list[dict]) -> list[dict]:
    rng = random.Random(20261018)
    rows = []
    for day in range(DAYS):
        for bus in fleet:
            depot = bus['depot_id']
            routes = sorted(ROUTES[depot])
            route = routes[(day + int(bus['bus_id'][-1])) % len(routes)]
            for run in range(2):
                dep = START + dt.timedelta(days=day, hours=run * 9 + rng.randint(0, 3), minutes=rng.choice([0, 15, 30, 45]))
                cancelled = rng.random() < 0.03
                delay, stops = 0.0, []
                if not cancelled:
                    peak = 1.6 if dep.hour in (7, 8, 16, 17) else 1.0
                    for stop in ROUTES[depot][route]:
                        delay = max(-1.0, round(delay + rng.gauss(CONGESTION[route] * peak, 1.2) - 0.6, 1))
                        stops.append({'stop': stop, 'delay': delay})
                rows.append(dict(
                    trip_id=f"{bus['bus_id']}-{dep:%m%d}-{run}", bus_id=bus['bus_id'], depot_id=depot, route=route,
                    departure=dep, passengers=0 if cancelled else rng.randint(6, 70), stops=stops,
                    telemetry=telemetry(rng, bus),
                ))
    return rows


def main() -> None:
    fleet = buses()
    pxt.get_table(f'{CATALOG}/depots').insert(DEPOTS, on_error='ignore')
    pxt.get_table(f'{CATALOG}/buses').insert(fleet, on_error='ignore')
    status = pxt.get_table(f'{CATALOG}/trips').insert(trips(fleet), on_error='ignore')
    print(f'trips: {status.num_rows} rows, {status.num_excs} errors')


if __name__ == '__main__':
    main()
