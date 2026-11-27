"""Lotsningar till och från Hargshamn.

Läser Sjöfartsverkets publika lotsplan (app.sjofartsverket.se/pilotinfo) och sparar
lotsningarna som rör Hargshamn i data/lots.json. Uppgifterna är planeringsstöd och
kan enligt Sjöfartsverket inte användas för att rikta anspråk mot verket.

Körs från samma jobb som AIS-loggen (var tionde minut) men hämtar bara under de första
tio minuterna av varje halvtimme, alltså ungefär var 30:e minut.
Avslutade lotsningar sparas i 90 dagar som underlag för uppföljningen.
"""
import html, http.cookiejar, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import logg

TZ = ZoneInfo('Europe/Stockholm')
BASE = 'https://app.sjofartsverket.se/pilotinfo'
KEEP = timedelta(days=90)
DAYS_BACK, DAYS_AHEAD = 2, 5
KEYS = ['ordered', 'planned', 'status', 'name', 'from', 'to', 'started', 'finished', 'assigned']
KAJ = re.compile(r'^\s*(\d+)\s*-\s*(.+?)\s*-\s*Hargshamn\s*$', re.I)

opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
opener.addheaders = [('User-Agent', 'HargsHamnKMA/1.0 (planeringsstod, kontakt alex@lexprocura.se)')]


def get(url):
    with opener.open(url, timeout=60) as r:
        return r.read().decode('utf-8', 'replace')


def cells(tr):
    out = []
    for td in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S):
        td = re.sub(r'<a[^>]*mobile-th[^>]*>.*?</a>', '', td, flags=re.S)
        td = re.sub(r'<span[^>]*mobile-th[^>]*>.*?</span>', '', td, flags=re.S)
        out.append(html.unescape(re.sub(r'<[^>]+>', ' ', td)).split())
    return [' '.join(c) for c in out]


def rows(page):
    body = page.split('<tbody', 1)[-1].split('</tbody>', 1)[0]
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.S):
        c = cells(tr)
        if len(c) == len(KEYS):
            yield dict(zip(KEYS, c))


def day(d):
    s = d.strftime('%Y-%m-%d')
    first = get(BASE + '?' + urllib.parse.urlencode({'SearchFrom': s, 'SearchTo': s, 'TableSize': 100}))
    out = list(rows(first))
    m = re.search(r'Visar \d+ till \d+ av (\d+) rader', first)
    total = int(m.group(1)) if m else len(out)
    page = 2
    while len(out) < total and page <= 10:
        q = {'userAction': 'page', 'tableSize': 100, 'pageIndex': page,
             'searchTo': s + ' 23:59:59', 'searchFrom': s + ' 00:00:00'}
        more = list(rows(get(BASE + '?' + urllib.parse.urlencode(q))))
        if not more:
            break
        out += more
        page += 1
    return out


def iso(s):
    """'2026-11-28 09:00' svensk tid till UTC-sträng, tom sträng blir None."""
    if not s:
        return None
    try:
        d = datetime.strptime(s.strip()[:16], '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
    except ValueError:
        return None
    return d.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def normalize(r):
    fr, to = r['from'], r['to']
    inbound = bool(re.search('hargshamn', to, re.I))
    m = KAJ.match(to if inbound else fr)
    return {
        'name': r['name'].strip().upper(), 'dir': 'in' if inbound else 'ut', 'from': fr, 'to': to,
        'kaj': int(m.group(1)) if m else None, 'kajnamn': m.group(2) if m else None,
        'status': r['status'], 'ordered': iso(r['ordered']), 'planned': iso(r['planned']),
        'started': iso(r['started']), 'finished': iso(r['finished']), 'assigned': r['assigned'].lower().startswith('ja'),
    }


def main():
    now = datetime.now(timezone.utc)
    old = logg.load('lots.json', {'calls': []})
    if now.minute % 30 >= 10 and old.get('calls') is not None and 'updated' in old and '--nu' not in sys.argv:
        print('Lotsplanen hämtas bara i början av varje halvtimme.')
        return
    today = now.astimezone(TZ).date()
    found = {}
    for i in range(-DAYS_BACK, DAYS_AHEAD + 1):
        for r in day(today + timedelta(days=i)):
            if re.search('hargshamn', r['from'] + ' ' + r['to'], re.I):
                c = normalize(r)
                found[(c['name'], c['dir'], c['ordered'])] = c
    # Äldre lotsningar sparas bara om de ligger före sökfönstret. Inom fönstret gäller det
    # som syns nu, så att ändrade eller strukna beställningar inte blir kvar.
    start = datetime.combine(today - timedelta(days=DAYS_BACK), datetime.min.time(), TZ).astimezone(timezone.utc)
    keep = {}
    for c in old.get('calls', []):
        t = c.get('planned') or c.get('ordered')
        if not t:
            continue
        t = datetime.strptime(t, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        if t < start and now - t < KEEP:
            keep[(c['name'], c['dir'], c['ordered'])] = c
    keep.update(found)
    calls = sorted(keep.values(), key=lambda c: c.get('planned') or c.get('ordered') or '', reverse=True)
    if calls != old.get('calls'):
        logg.save('lots.json', {'source': BASE, 'updated': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'calls': calls})
        print('Lotsningar Hargshamn: %d, varav %d i sökfönstret.' % (len(calls), len(found)))
    else:
        print('Inga ändringar i lotsplanen.')


if __name__ == '__main__':
    main()
