"""Väder och vattenstånd för Hargshamn, sparat timme för timme.

Hämtar SMHI:s senaste mätvärden och sparar dem i data/vader/ÅÅÅÅ-MM.json, en rad per timme.
Loggen är underlag för uppföljningen av stuveriavtalet, till exempel för att se vilken vind
som rådde under en lossning.

Vind: SMHI:s station Örskär A (108320), som ligger ute vid kusten norr om Öregrund.
Vattenstånd: SMHI:s station Forsmark (2179), relativt årets medelvattenstånd (RW).
Källa: SMHI, öppna data under licensen CC BY 4.0.
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import logg

MET = 'https://opendata-download-metobs.smhi.se/api/version/latest/parameter/%s/station/%s/period/latest-hour/data.json'
OCE = 'https://opendata-download-ocobs.smhi.se/api/version/latest/parameter/%s/station/%s/period/latest-day/data.json'
WIND_STATION, SEA_STATION = '108320', '2179'
PARAMS = {'wind': ('4', WIND_STATION), 'gust': ('21', WIND_STATION), 'dir': ('3', WIND_STATION), 'temp': ('1', WIND_STATION)}


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'HargsHamnKMA/1.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def main():
    row = {}
    for key, (p, st) in PARAMS.items():
        try:
            v = (fetch(MET % (p, st)).get('value') or [])[-1]
            row['time'] = iso(v['date'])
            row[key] = float(v['value'])
        except Exception as e:
            print('Saknar %s: %s' % (key, e))
    try:
        vals = fetch(OCE % ('12', SEA_STATION)).get('value') or []
        hour = row.get('time')
        match = [v for v in vals if iso(v['date']) == hour] or vals[-1:]
        if match:
            row['sea_rw_cm'] = float(match[-1]['value'])
            row.setdefault('time', iso(match[-1]['date']))
    except Exception as e:
        print('Saknar vattenstånd:', e)
    if 'time' not in row:
        print('Inga mätvärden.')
        return
    name = os.path.join('vader', row['time'][:7] + '.json')
    rows = logg.load(name, [])
    if any(r.get('time') == row['time'] for r in rows):
        print('Timmen %s finns redan.' % row['time'])
        return
    rows.append(row)
    os.makedirs(os.path.join(logg.DATA, 'vader'), exist_ok=True)
    logg.save(name, sorted(rows, key=lambda r: r['time']))
    print('Sparat väder', row)


if __name__ == '__main__':
    main()
