"""Sanktionskontroll av fartyg i Hargshamn.

Hämtar tre offentliga listor två gånger per dygn och sparar dem i data/sanktion.json:
  EU  Danska Sjöfartsstyrelsen (DMA), EU-listade fartyg: bilaga XLII till
      förordning 833/2014 (Ryssland, förbud mot hamnanlöp) samt Nordkorea och Libyen
  UK  FCDO, UK Sanctions List (fartyg)
  US  OFAC, SDN-listan (fartyg)

Vid varje körning jämförs fartygen vid kaj, på redden, på väg in och i lotsplanen
mot listorna. Nya träffar skickas som larm via e-post till SANKTION_TO (eller MAIL_TO).
Webbsidan läser samma fil och visar träffarna i fartygskorten.
"""
import csv, html, io, json, os, re, smtplib, ssl, sys, urllib.request
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import logg

DMA = 'https://www.dma.dk/growth-and-framework-conditions/maritime-sanctions/general-information/eu-vessel-designations'
UK = 'https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv'
US = ['https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV',
      'https://www.treasury.gov/ofac/downloads/sdn.csv']
REFRESH_H = 12
DEST = re.compile(r'HARG|SE\s*HAN', re.I)
IMO = re.compile(r'IMO\s*:?\s*(\d{7})', re.I)
LABEL = {'EU': 'EU', 'UK': 'Storbritannien', 'US': 'USA (OFAC)'}


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Hargshamn fartygslogg)'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode('utf-8-sig', errors='replace')


def norm(name):
    """Namn för jämförelse: versaler, bara bokstäver och siffror."""
    return re.sub(r'[^A-Z0-9]+', ' ', (name or '').upper()).strip()


def eu_list():
    """Tabellerna på DMA:s sida: först bilaga XLII, sedan Nordkorea och Libyen."""
    page = fetch(DMA)
    out, regimes = {}, ['bilaga XLII (Ryssland)', 'Nordkorea', 'Libyen']
    for i, tab in enumerate(re.findall(r'<table.*?</table>', page, re.S | re.I)):
        for row in re.findall(r'<tr.*?</tr>', tab, re.S | re.I):
            cells = [html.unescape(re.sub(r'<[^>]+>', ' ', c)).strip() for c in re.findall(r'<td.*?</td>', row, re.S | re.I)]
            cells = [re.sub(r'\s+', ' ', c) for c in cells]
            if len(cells) < 2 or not re.fullmatch(r'\d{7}', cells[1]):
                continue
            names = [n.strip() for n in re.split(r'\(formerly|\(ex\.?|\)', cells[0], flags=re.I) if n.strip()]
            out[cells[1]] = {'n': names, 'r': regimes[i] if i < len(regimes) else 'EU'}
    if len(out) < 100:
        raise ValueError('för få fartyg på DMA-sidan (%d), sidan kan ha ändrats' % len(out))
    return out


def uk_list():
    """UK Sanctions List som CSV. Rubrikraden letas upp, fartyg känns igen på IMO-nummer."""
    rows = list(csv.reader(io.StringIO(fetch(UK))))
    hi = next(i for i, r in enumerate(rows[:10]) if any('IMO' in c for c in r))
    head = [c.strip().lower() for c in rows[hi]]
    ci = next(i for i, c in enumerate(head) if 'imo' in c)
    ni = next((i for i, c in enumerate(head) if c in ('name 6', 'name6')), None)
    out = {}
    for r in rows[hi + 1:]:
        if len(r) <= ci:
            continue
        m = IMO.search(r[ci]) or (re.fullmatch(r'\s*(\d{7})\s*', r[ci]))
        if not m:
            continue
        e = out.setdefault(m.group(1), {'n': [], 'r': 'Russia' if any('Russia' in c for c in r) else ''})
        nm = r[ni].strip() if ni is not None and len(r) > ni else ''
        nm = re.sub(r'^IMO\s*\d{7}\s*', '', nm).strip(' ()"\'')
        if nm and nm not in e['n']:
            e['n'].append(nm)
    if len(out) < 50:
        raise ValueError('för få fartyg i UK-listan (%d)' % len(out))
    return out


def us_list():
    """OFAC SDN.CSV utan rubrikrad: kolumn 1 namn, 2 typ ('vessel'), 3 program, 11 anmärkningar."""
    last = None
    for url in US:
        try:
            text = fetch(url)
            break
        except Exception as ex:
            last = ex
    else:
        raise last
    out = {}
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 12 or r[2].strip().lower() != 'vessel':
            continue
        m = IMO.search(r[11])
        if m:
            out[m.group(1)] = {'n': [r[1].strip()], 'r': r[3].strip()}
    if len(out) < 50:
        raise ValueError('för få fartyg i OFAC-listan (%d)' % len(out))
    return out


def refresh(old, now):
    """Hämtar listorna om de är äldre än REFRESH_H timmar. En lista som inte går att hämta behåller sitt gamla innehåll."""
    if old.get('checked') and now - datetime.fromisoformat(old['checked'].replace('Z', '+00:00')) < timedelta(hours=REFRESH_H):
        return old, False
    lists = {k: v for k, v in (old.get('lists') or {}).items()}
    src = dict(old.get('sources') or {})
    for key, fn in (('EU', eu_list), ('UK', uk_list), ('US', us_list)):
        try:
            lists[key] = fn()
            src[key] = {'ok': True, 'at': logg.iso(now.timestamp() * 1000), 'n': len(lists[key])}
        except Exception as ex:
            print('Kunde inte hämta %s-listan: %s' % (key, ex))
            s = src.get(key) or {}
            src[key] = {'ok': False, 'at': s.get('at'), 'n': len(lists.get(key) or {}), 'err': str(ex)[:200]}
    return {'checked': logg.iso(now.timestamp() * 1000), 'sources': src, 'lists': lists}, True


def compact(data):
    """Det som webbsidan behöver: IMO och namn per lista."""
    imo, names = {}, {}
    for key, lst in (data.get('lists') or {}).items():
        for nr, e in lst.items():
            imo.setdefault(nr, {})[key] = e.get('r') or ''
            for n in e.get('n') or []:
                n = norm(n)
                if len(n) >= 4:
                    names.setdefault(n, [])
                    if key not in names[n]:
                        names[n].append(key)
    return {'checked': data.get('checked'), 'sources': data.get('sources'), 'imo': imo, 'names': names}


def candidates(now):
    """Fartyg att kontrollera: i loggen (vid kaj, redden), på väg in enligt AIS och i lotsplanen."""
    out = {}
    for m, s in (logg.load('state.json', {}).get('ships') or {}).items():
        out[('M', m)] = {'name': s.get('name'), 'imo': str(s.get('imo') or ''), 'mmsi': m, 'var': 'i Hargshamn'}
    try:
        since = int((now - timedelta(days=7)).timestamp() * 1000)
        for v in logg.get('/vessels?from=%d' % since):
            if DEST.search(v.get('destination') or ''):
                m = str(v.get('mmsi'))
                out.setdefault(('M', m), {'name': (v.get('name') or '').strip(), 'imo': str(v.get('imo') or ''), 'mmsi': m, 'var': 'på väg till Hargshamn'})
    except Exception as ex:
        print('Kunde inte hämta fartyg på väg:', ex)
    for c in (logg.load('lots.json', {}).get('calls') or []):
        if c.get('status') != 'Avslutad':
            when = datetime.fromisoformat(c['planned'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Stockholm')).strftime('%y-%m-%d %H:%M') if c.get('planned') else ''
            out.setdefault(('N', norm(c['name'])), {'name': c['name'], 'imo': '', 'mmsi': '', 'var': 'i lotsplanen, lots %s %s' % (c['dir'], when)})
    return list(out.values())


def match(c, comp):
    """Träff på IMO (säker) eller bara på namn (möjlig)."""
    imo = c['imo'] if re.fullmatch(r'\d{7}', c['imo'] or '') and c['imo'] != '0000000' else ''
    if imo and imo in comp['imo']:
        return {'sure': True, 'lists': comp['imo'][imo]}
    n = norm(c['name'])
    if n and n in comp['names'] and not imo:
        return {'sure': False, 'lists': {k: '' for k in comp['names'][n]}}
    return None


def alert(hits):
    need = ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'MAIL_FROM']
    to = os.environ.get('SANKTION_TO') or os.environ.get('MAIL_TO') or ''
    to = [x.strip() for x in to.split(',') if x.strip()]
    if not all(os.environ.get(k) for k in need) or not to:
        print('E-postinställningar saknas, inget larm skickas.')
        return False
    lines = []
    for c, h in hits:
        lst = ', '.join('%s%s' % (LABEL.get(k, k), (' ' + r) if r else '') for k, r in h['lists'].items())
        lines.append('%s%s, %s: %s %s' % (c['name'], (' (IMO %s)' % c['imo']) if c['imo'] else '', c['var'],
                                          'finns på' if h['sure'] else 'namnet matchar, IMO saknas, kontrollera:', lst))
    sure = any(h['sure'] for _, h in hits)
    msg = EmailMessage()
    msg['Subject'] = ('Sanktionsträff' if sure else 'Möjlig sanktionsträff') + ': ' + ', '.join(c['name'] for c, _ in hits)
    msg['From'] = os.environ['MAIL_FROM']
    msg['To'] = ', '.join(to)
    msg.set_content('Sanktionskontrollen för Hargshamn har hittat:\n\n' + '\n'.join('- ' + x for x in lines)
                    + '\n\nKontrollera uppgifterna mot listan innan beslut. EU-listan: ' + DMA
                    + '\nSe fartyget på https://lanefelt.github.io/hargshamn-fartyg/\n')
    port = int(os.environ['SMTP_PORT'])
    if port == 465:
        with smtplib.SMTP_SSL(os.environ['SMTP_HOST'], port, context=ssl.create_default_context()) as s:
            s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
            s.send_message(msg)
    else:
        with smtplib.SMTP(os.environ['SMTP_HOST'], port) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
            s.send_message(msg)
    print('Larm skickat till %d mottagare.' % len(to))
    return True


def main():
    now = datetime.now(timezone.utc)
    old = logg.load('sanktion_listor.json', {})
    data, changed = refresh(old, now)
    if changed:
        logg.save('sanktion_listor.json', data)
    comp = compact(data)

    # träffar just nu; larm bara för nya
    seen = logg.load('sanktion_larm.json', {})
    hits, new = {}, []
    for c in candidates(now):
        h = match(c, comp)
        if not h:
            continue
        key = c['imo'] or norm(c['name'])
        hits[key] = {'name': c['name'], 'imo': c['imo'], 'mmsi': c['mmsi'], 'sure': h['sure'], 'lists': h['lists'], 'var': c['var']}
        if key not in seen:
            new.append((c, h))
    comp['hits'] = hits
    if new:
        try:
            if alert(new):
                for c, _ in new:
                    seen[c['imo'] or norm(c['name'])] = logg.iso(now.timestamp() * 1000)
                logg.save('sanktion_larm.json', seen)
        except Exception as ex:
            print('Kunde inte skicka larm:', ex)

    prev = logg.load('sanktion.json', {})
    if changed or prev.get('hits') != hits:
        # kompakt, webbsidan hämtar filen vid varje besök
        with open(os.path.join(logg.DATA, 'sanktion.json'), 'w', encoding='utf-8') as f:
            json.dump(comp, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    print('Sanktionskontroll: %d träffar, listor %s' % (len(hits), ', '.join('%s %s' % (k, v.get('n')) for k, v in comp['sources'].items())))


if __name__ == '__main__':
    main()
