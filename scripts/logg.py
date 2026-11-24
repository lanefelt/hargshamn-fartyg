"""AIS-logg för Hargshamn.

Körs var tionde minut av GitHub Actions. Hämtar öppen AIS-data från Fintraffic
(digitraffic.fi, CC BY 4.0) och loggar när last- och tankfartyg kommer till
redden, lägger till vid kaj och lämnar kaj.

data/state.json  fartyg som just nu är i närområdet och följs
data/anlop.json  avslutade anlöp (senaste överst)
"""
import json, math, os, urllib.request, gzip
from datetime import datetime, timezone

API = 'https://meri.digitraffic.fi/api/ais/v1'
PORT = (60.1686, 18.4890)   # kajerna i Hargshamn
QUAY_KM = 1.2               # kajzon
NEAR_KM = 25                # närområde, redd och inlopp
DATA = os.path.join(os.path.dirname(__file__), '..', 'data')


def get(path):
    req = urllib.request.Request(API + path, headers={'Digitraffic-User': 'HargsHamnKMA/1.0', 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
        return json.loads(raw)


def km(a, b):
    r = math.radians
    dla, dlo = r(b[0] - a[0]), r(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlo / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return default


def save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write('\n')


def hours(a, b):
    if not a or not b:
        return None
    t = lambda s: datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ')
    return round((t(b) - t(a)).total_seconds() / 3600, 1)


def main():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    state = load('state.json', {'started': now, 'ships': {}})
    first_run = not state['ships'] and not os.path.exists(os.path.join(DATA, 'state.json'))
    anlop = load('anlop.json', [])

    feats = get('/locations?latitude=%s&longitude=%s&radius=%s' % (PORT[0], PORT[1], NEAR_KM))['features']
    seen = {}
    for f in feats:
        p = f['properties']
        lon, lat = f['geometry']['coordinates']
        seen[str(p['mmsi'])] = {'lat': lat, 'lon': lon, 'sog': p.get('sog') or 0, 'nav': p.get('navStat'), 'ts': p.get('timestampExternal')}

    # fartyg vi följer vid kaj men som saknas i närområdet: hämta senaste position
    for mmsi, s in state['ships'].items():
        if mmsi not in seen and s.get('arrival') is not None:
            try:
                f = get('/locations/' + mmsi)
                lon, lat = f['geometry']['coordinates']
                p = f['properties']
                seen[mmsi] = {'lat': lat, 'lon': lon, 'sog': p.get('sog') or 0, 'nav': p.get('navStat'), 'ts': p.get('timestampExternal')}
            except Exception:
                pass

    ships = state['ships']
    for mmsi, pos in seen.items():
        dist = km(PORT, (pos['lat'], pos['lon']))
        still = pos['sog'] < 0.5
        at_quay = dist <= QUAY_KM and still
        waiting = still and not at_quay and pos['nav'] != 5 and dist <= NEAR_KM
        s = ships.get(mmsi)
        if s is None:
            if not (at_quay or waiting):
                continue
            try:
                v = get('/vessels/' + mmsi)
            except Exception:
                continue
            if not (70 <= (v.get('shipType') or 0) < 90):
                continue
            s = ships[mmsi] = {'name': (v.get('name') or '').strip() or 'MMSI ' + mmsi, 'imo': v.get('imo'),
                               'shipType': v.get('shipType'), 'first_seen': now, 'anchor_start': None, 'arrival': None,
                               'before_log': False}
            if at_quay and first_run:
                s['before_log'] = True      # låg redan vid kaj när loggningen startade
            elif at_quay:
                s['arrival'] = iso(pos['ts']) if pos['ts'] else now
        if at_quay and s['arrival'] is None and not s['before_log']:
            s['arrival'] = now
        if waiting and s['arrival'] is None and not s['before_log'] and s['anchor_start'] is None:
            s['anchor_start'] = now

    # avgångar och fartyg som försvunnit
    for mmsi in list(ships):
        s = ships[mmsi]
        pos = seen.get(mmsi)
        docked = s['arrival'] is not None or s['before_log']
        if pos is not None:
            dist = km(PORT, (pos['lat'], pos['lon']))
            at_quay = dist <= QUAY_KM and pos['sog'] < 0.5
        else:
            dist, at_quay = None, False
        if docked and not at_quay and (pos is None or dist > QUAY_KM or pos['sog'] >= 0.5):
            if pos is not None and dist is not None and dist <= QUAY_KM:
                continue                    # rör sig inne i kajzonen, t.ex. förhalning
            anlop.insert(0, {'mmsi': mmsi, 'name': s['name'], 'imo': s['imo'],
                             'anchor_start': s['anchor_start'], 'arrival': s['arrival'], 'departure': now,
                             'before_log': s['before_log'],
                             'hours_at_quay': hours(s['arrival'], now),
                             'hours_waiting': hours(s['anchor_start'], s['arrival'])})
            del ships[mmsi]
        elif not docked and pos is None:
            del ships[mmsi]                 # passerade utan att lägga till

    state['ships'] = ships
    save('state.json', state)
    save('anlop.json', anlop[:500])


if __name__ == '__main__':
    main()
