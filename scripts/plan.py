"""Båtlista för Hargshamn.

Hämtar mäklarens planerade anlöp från formuläret "Båtlista Hargshamn" och sparar
dem i data/plan.json för sidan Båtlista. Körs i samma flöde som AIS-loggen.

Bara uppgifter som får visas öppet sparas: fartyg, IMO, status, tider, kaj,
lossning eller lastning och godsslag. Kund, mängd och kontaktuppgifter stannar
i formuläret. Inställda anlöp och anlöp som är äldre än 14 dagar tas inte med.
Kräver JOTFORM_API_KEY. Saknas den görs ingenting.
"""
import json, os, re, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FORM_ID = '263291180653053'
TZ = ZoneInfo('Europe/Stockholm')
DATA = os.path.join(os.path.dirname(__file__), '..', 'data')


def norm(s):
    return re.sub(r'[^a-zåäö]', '', (s or '').lower())


def value(a):
    """Svaret som text, eller som dict för datum och tid."""
    v = a.get('answer')
    if isinstance(v, dict):
        return v
    return (str(v).strip() if v is not None else '')


def when(v, with_time=True):
    """Jotforms datum (med eller utan tid) till ISO-tid i UTC, eller datum."""
    if isinstance(v, dict):
        try:
            y, m, d = int(v.get('year')), int(v.get('month')), int(v.get('day'))
        except (TypeError, ValueError):
            return None
        hh, mm = 0, 0
        if with_time:
            try:
                hh, mm = int(v.get('hour') or 0), int(v.get('min') or 0)
            except ValueError:
                pass
            ampm = (v.get('ampm') or '').upper()
            if ampm == 'PM' and hh < 12: hh += 12
            if ampm == 'AM' and hh == 12: hh = 0
    else:
        m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?', v or '')
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        m_, hh, mm = mo, int(m.group(4) or 0), int(m.group(5) or 0)
        m = m_
    if not with_time:
        return '%04d-%02d-%02d' % (y, m, d)
    local = datetime(y, m, d, hh, mm, tzinfo=TZ)
    return local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


FIELDS = {  # rubrik i formuläret -> nyckel i plan.json
    'fartyg': 'name', 'imonummer': 'imo', 'status': 'status', 'beräknadankomsteta': 'eta',
    'beräknatklart': 'done', 'kaj': 'kaj', 'lossningellerlastning': 'op', 'gods': 'gods',
}


def main():
    key = os.environ.get('JOTFORM_API_KEY')
    if not key:
        print('JOTFORM_API_KEY saknas, båtlistan uppdateras inte.')
        return
    url = 'https://eu-api.jotform.com/form/%s/submissions?apiKey=%s&limit=1000&orderby=created_at,ASC' % (FORM_ID, key)
    with urllib.request.urlopen(url, timeout=60) as r:
        subs = json.load(r).get('content', [])
    limit = datetime.now(timezone.utc) - timedelta(days=14)
    calls = []
    for sub in subs:
        if sub.get('status') == 'DELETED':
            continue
        c = {}
        for a in (sub.get('answers') or {}).values():
            k = FIELDS.get(norm(a.get('text')))
            if k:
                c[k] = value(a)
        if not c.get('name') or str(c.get('status', '')).startswith('Inställ'):
            continue
        eta = when(c.get('eta'))
        if not eta or datetime.strptime(eta, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) < limit:
            continue
        kaj = str(c.get('kaj') or '')
        calls.append({
            'name': str(c['name']).upper(),
            'imo': re.sub(r'\D', '', str(c.get('imo') or '')) or None,
            'status': c.get('status') or 'Preliminär',
            'eta': eta,
            'done': when(c.get('done'), with_time=False),
            'kaj': int(kaj[0]) if kaj[:1].isdigit() else None,
            'kajnamn': kaj[2:].strip() if kaj[:1].isdigit() else 'Ej bestämd',
            'op': c.get('op') or '',
            'gods': c.get('gods') or '',
            'updated': (sub.get('updated_at') or sub.get('created_at') or '').replace(' ', 'T'),
        })
    calls.sort(key=lambda x: x['eta'])
    out = {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'calls': calls}
    path = os.path.join(DATA, 'plan.json')
    old = None
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            old = json.load(f)
    if old and old.get('calls') == calls:
        print('Båtlistan oförändrad (%d anlöp).' % len(calls))
        return
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('Båtlistan sparad (%d anlöp).' % len(calls))


if __name__ == '__main__':
    main()
