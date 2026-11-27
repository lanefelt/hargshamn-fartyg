"""Daglig fartygsrapport för Hargshamn via e-post.

Läser loggen i data/ och aktuella AIS-positioner och skickar en sammanfattning.
Inställningarna ligger som hemliga värden i repot (Settings > Secrets and variables > Actions):
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM och JOTFORM_API_KEY.
MAIL_TO (valfritt) är fasta mottagare, flera adresser separeras med komma. De får också en rad
om vilka som får mejlet.
Övriga mottagare hämtas från formuläret "Prenumerera på fartygslistan". Senaste anmälan per
adress gäller, och bara adresser på @hargshamn.se och @sveahamn.se tas med.
"""
import os, sys, smtplib, ssl, json, re, urllib.request
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import logg

TZ = ZoneInfo('Europe/Stockholm')
PAGE = 'https://lanefelt.github.io/hargshamn-fartyg/'
FORM_ID = '263273393605056'
FORM_URL = PAGE + '#prenumerera'
DOMAINS = re.compile(r'^[^@\s]+@(hargshamn|sveahamn)\.se$', re.I)


def subscribers():
    key = os.environ.get('JOTFORM_API_KEY')
    if not key:
        return []
    url = 'https://eu-api.jotform.com/form/%s/submissions?apiKey=%s&limit=1000&orderby=created_at,ASC' % (FORM_ID, key)
    with urllib.request.urlopen(url, timeout=60) as r:
        subs = json.load(r).get('content', [])
    active = {}
    for sub in sorted(subs, key=lambda x: x.get('created_at', '')):
        if sub.get('status') == 'DELETED':
            continue
        mail, choice = None, ''
        for a in (sub.get('answers') or {}).values():
            if a.get('type') == 'control_email' and a.get('answer'):
                mail = a['answer'].strip().lower()
            elif a.get('type') in ('control_radio', 'control_dropdown') and a.get('answer'):
                choice = str(a['answer'])
        if not mail or not DOMAINS.match(mail):
            continue
        if choice.startswith('Avsluta'):
            active.pop(mail, None)
        else:
            active[mail] = True
    return sorted(active)


def t(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) if s else None


def fmt(s):
    d = t(s)
    return d.astimezone(TZ).strftime('%y-%m-%d %H:%M') if d else ''


def dur(h):
    if h is None:
        return ''
    return ('%.1f tim' % h if h < 48 else '%.1f dygn' % (h / 24)).replace('.', ',')


NAVY, CYAN, BLUE, INK, MUTED, LINE, BG = '#0B1F33', '#2BA8E0', '#0A629E', '#1F2933', '#52606D', '#D9E2EC', '#F4F7FA'
GREEN, ORANGE = '#2E7D32', '#C2410C'
DEST = re.compile(r'HARG|SE\s*HAN', re.I)
DAGAR = ['Måndag', 'Tisdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lördag', 'Söndag']
MAN = ['januari', 'februari', 'mars', 'april', 'maj', 'juni', 'juli', 'augusti', 'september', 'oktober', 'november', 'december']
MID = {209: 'Cypern', 210: 'Cypern', 212: 'Cypern', 211: 'Tyskland', 218: 'Tyskland', 215: 'Malta', 229: 'Malta', 248: 'Malta',
       249: 'Malta', 256: 'Malta', 219: 'Danmark', 220: 'Danmark', 230: 'Finland', 232: 'Storbritannien', 233: 'Storbritannien',
       234: 'Storbritannien', 235: 'Storbritannien', 236: 'Gibraltar', 244: 'Nederländerna', 245: 'Nederländerna',
       246: 'Nederländerna', 247: 'Italien', 250: 'Irland', 255: 'Portugal', 257: 'Norge', 258: 'Norge', 259: 'Norge',
       261: 'Polen', 265: 'Sverige', 266: 'Sverige', 271: 'Turkiet', 273: 'Ryssland', 275: 'Lettland', 276: 'Estland',
       277: 'Litauen', 304: 'Antigua och Barbuda', 305: 'Antigua och Barbuda', 308: 'Bahamas', 309: 'Bahamas', 311: 'Bahamas',
       351: 'Panama', 352: 'Panama', 353: 'Panama', 354: 'Panama', 355: 'Panama', 356: 'Panama', 357: 'Panama', 370: 'Panama',
       371: 'Panama', 372: 'Panama', 373: 'Panama', 538: 'Marshallöarna', 563: 'Singapore', 564: 'Singapore', 565: 'Singapore',
       566: 'Singapore', 636: 'Liberia', 637: 'Liberia', 314: 'Barbados', 341: 'Saint Kitts och Nevis', 375: 'Saint Vincent',
       376: 'Saint Vincent', 377: 'Saint Vincent', 477: 'Hongkong'}


def typ(st):
    st = st or 0
    return 'Lastfartyg' if 70 <= st < 80 else 'Tankfartyg' if 80 <= st < 90 else 'Fartyg'


def cap(x):
    return re.sub(r'(^|[\s\-/])(\S)', lambda m: m.group(1) + m.group(2).upper(), (x or '').strip().lower())


DESTS = {}


def info(mmsi, st=None):
    """Typ, flagg och storlek. Storleken hämtas från Digitraffic och kan saknas."""
    parts = [typ(st)]
    try:
        flag = MID.get(int(str(mmsi)[:3]))
        if flag:
            parts.append(flag)
    except ValueError:
        pass
    size = ''
    try:
        v = logg.get('/vessels/' + str(mmsi))
        ln = (v.get('referencePointA') or 0) + (v.get('referencePointB') or 0)
        br = (v.get('referencePointC') or 0) + (v.get('referencePointD') or 0)
        if ln and br:
            size = '%d × %d m' % (ln, br)
        DESTS[str(mmsi)] = v.get('destination') or ''
        if st is None:
            parts[0] = typ(v.get('shipType'))
    except Exception:
        pass
    return ' · '.join(parts), size


def eta(e, now):
    if not e:
        return None
    mo, d, h, mi = (e >> 16) & 15, (e >> 11) & 31, (e >> 6) & 31, e & 63
    if not mo or not d or h > 23 or mi > 59:
        return None
    try:
        x = datetime(now.year, mo, d, h, mi, tzinfo=timezone.utc)
    except ValueError:
        return None
    if x < now - timedelta(days=180):
        x = x.replace(year=now.year + 1)
    return x


def on_the_way(ships, now):
    """Last- och tankfartyg som angett Hargshamn som destination och inte redan är här."""
    try:
        since = int((now - timedelta(days=7)).timestamp() * 1000)
        out = []
        for v in logg.get('/vessels?from=%d' % since):
            m = str(v.get('mmsi'))
            if m in ships or not DEST.search(v.get('destination') or '') or not (70 <= (v.get('shipType') or 0) < 90):
                continue
            e = eta(v.get('eta'), now)
            if e and e < now - timedelta(hours=12):
                continue
            out.append((e, (v.get('name') or 'MMSI ' + m).strip(), m, v.get('shipType')))
        return sorted(out, key=lambda x: x[0] or now + timedelta(days=999))
    except Exception as ex:
        print('Kunde inte hämta fartyg på väg:', ex)
        return []


def head(text, c):
    return '<div style="font-size:12px;font-weight:bold;letter-spacing:2px;color:%s;margin:22px 4px 10px">&#9679; %s</div>' % (c, text)


def card(c, name, tag, pin, sub, right='', foot=''):
    """Fartygskort i samma upplägg som webbsidan: namn och typ, nål med kaj, detaljer och lotsrad."""
    pin = ('<div style="font-size:13px;color:%s;margin-top:3px">&#128205; %s</div>' % (INK, pin)) if pin else ''
    sub = ('<div style="font-size:12px;color:%s;margin-top:2px">%s</div>' % (MUTED, sub)) if sub else ''
    foot = ('<div style="font-size:12px;color:%s;margin-top:8px;padding-top:7px;border-top:1px dashed %s;line-height:1.6">%s</div>' % (INK, LINE, foot)) if foot else ''
    return ('<div style="background:#ffffff;border-radius:8px;padding:14px 16px;margin:0 0 10px;font-family:Arial,Helvetica,sans-serif">'
            '<table role="presentation" width="100%%" cellspacing="0" cellpadding="0"><tr><td valign="top">'
            '<span style="font-size:17px;font-weight:bold;color:%s">%s</span>'
            ' <span style="font-size:10px;font-weight:bold;letter-spacing:1px;color:%s;background:#F0F4F8;padding:2px 6px;border-radius:3px;vertical-align:2px">%s</span>'
            '%s%s</td><td align="right" valign="top" style="font-size:15px;font-weight:bold;color:%s;white-space:nowrap;padding-left:8px">%s</td></tr></table>%s</div>'
            ) % (NAVY, name, c, tag.upper(), pin, sub, NAVY, right, foot)


def empty(text):
    return '<div style="background:#ffffff;border-radius:8px;padding:12px 16px;margin:0 0 10px;font-size:13px;color:%s">%s</div>' % (MUTED, text)


def stat(n, label, c):
    return ('<td width="33%%" style="padding:0 4px"><div style="background:#ffffff;border-radius:8px;padding:12px 6px;text-align:center;border-bottom:4px solid %s">'
            '<div style="font-size:30px;font-weight:bold;color:%s;line-height:1;font-family:Arial,Helvetica,sans-serif">%d</div>'
            '<div style="font-size:11px;font-weight:bold;letter-spacing:1px;color:%s;margin-top:6px;font-family:Arial,Helvetica,sans-serif">%s</div></div></td>') % (c, NAVY, n, MUTED, label)


def timeline(events, now):
    """Händelser senaste dygnet, nyast först, grupperade på i dag och i går."""
    if not events:
        return empty('Inga ankomster eller avgångar senaste dygnet.')
    today = now.astimezone(TZ).date()
    ic = {'in': (GREEN, '&darr;'), 'ut': (BLUE, '&uarr;'), 'redd': (ORANGE, '&#9875;')}
    out, group = '', None
    for when, kind, txt in sorted(events, key=lambda x: x[0], reverse=True):
        local = when.astimezone(TZ)
        g = 'I natt och i morse' if local.date() == today else 'I går'
        if g != group:
            out += '<div style="font-size:11px;font-weight:bold;letter-spacing:2px;color:%s;margin:%s 0 8px">%s</div>' % (MUTED, '4px' if group is None else '14px', g.upper())
            group = g
        c, sym = ic[kind]
        out += ('<table role="presentation" width="100%%" cellspacing="0" cellpadding="0"><tr>'
                '<td width="48" valign="top" style="font-size:14px;font-weight:bold;color:%s;padding-top:6px;font-family:Arial,Helvetica,sans-serif">%s</td>'
                '<td width="30" valign="top" align="center"><div style="width:26px;height:26px;border-radius:13px;background:%s;color:#ffffff;font-size:14px;line-height:26px;text-align:center;font-family:Arial,Helvetica,sans-serif">%s</div>'
                '<div style="width:2px;height:16px;background:%s;margin:0 auto;font-size:0;line-height:0">&nbsp;</div></td>'
                '<td valign="top" style="font-size:14px;padding:5px 0 0 10px;color:%s;font-family:Arial,Helvetica,sans-serif">%s</td></tr></table>') % (
            NAVY, local.strftime('%H:%M'), c, sym, LINE, INK, txt)
    return '<div style="background:#ffffff;border-radius:8px;padding:14px 16px 4px">%s</div>' % out


def getj(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'HargsHamnKMA/1.0', **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


DIRS = ['N', 'NNO', 'NO', 'ONO', 'O', 'OSO', 'SO', 'SSO', 'S', 'SSV', 'SV', 'VSV', 'V', 'VNV', 'NV', 'NNV']
WARN_AREAS = re.compile(r'Uppsala|Östhammar|Ålands hav|Bottenhavet|Norra Östersjön|Norra Kvarken', re.I)
GUST_HI = 14
ODOR = (70, 110)  # östlig vind blåser in över samhället Hargshamn väster om hamnen


def weather(now):
    """Väder, vattenstånd, varningar och vintersjöfart. Det som inte går att hämta utelämnas."""
    w = {}
    try:
        fc = getj('https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1/geotype/point/lon/18.489/lat/60.169/data.json')
        ts = [(datetime.strptime(x['time'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc), x['data']) for x in fc['timeSeries']]
        ts = [x for x in ts if x[0] > now - timedelta(hours=1)]
        w['now'] = ts[0][1]
        day = [x for x in ts if x[0] <= now + timedelta(hours=24)]
        w['gust_max'] = max(day, key=lambda x: x[1].get('wind_speed_of_gust', 0))
        w['temps'] = (min(d['air_temperature'] for _, d in day), max(d['air_temperature'] for _, d in day))
        per, cur = [], None
        for t_, d in day:
            if ODOR[0] <= d.get('wind_from_direction', -1) <= ODOR[1]:
                if cur and t_ - cur[1] <= timedelta(hours=1):
                    cur[1] = t_
                else:
                    cur = [t_, t_]
                    per.append(cur)
            else:
                cur = None
        w['odor'] = per
    except Exception as e:
        print('Prognos saknas:', e)
    try:
        v = getj('https://opendata-download-ocobs.smhi.se/api/version/latest/parameter/12/station/2179/period/latest-day/data.json').get('value') or []
        if v:
            last = v[-1]
            prev = next((x for x in v if x['date'] >= last['date'] - 6 * 3600e3), None)
            w['sea'] = (float(last['value']), float(last['value']) - float(prev['value']) if prev else 0)
    except Exception as e:
        print('Vattenstånd saknas:', e)
    try:
        out = []
        for x in getj('https://opendata-download-warnings.smhi.se/ibww/api/version/1/warning.json'):
            if (x.get('event') or {}).get('code') == 'WATER_SHORTAGE':
                continue
            for a in x.get('warningAreas') or []:
                name = (a.get('areaName') or {}).get('sv') or ''
                if WARN_AREAS.search(name):
                    out.append('%s: %s, %s' % (((a.get('warningLevel') or {}).get('sv') or 'Varning'), x['event']['sv'], name))
        w['warn'] = out
    except Exception as e:
        print('Varningar saknas:', e)
    try:
        j = getj('https://meri.digitraffic.fi/api/winter-navigation/v2/locations', {'Digitraffic-User': 'HargsHamnKMA/1.0'})
        h = next((f['properties'] for f in j.get('features', []) if f['properties'].get('locodeList') == 'SEHAN'), None)
        if h is not None:
            def p(x):
                return datetime.fromisoformat(x).astimezone(timezone.utc) if x else None
            r = h.get('restrictions') or []
            cur = next((x for x in r if p(x['startTime']) <= now and (not x.get('endTime') or p(x['endTime']) > now)), None)
            nxt = sorted([x for x in r if p(x['startTime']) > now], key=lambda x: x['startTime'])
            w['ice'] = (cur, nxt[0] if nxt else None, p)
    except Exception as e:
        print('Vintersjöfart saknas:', e)
    return w


def ice_txt(x):
    m = re.match(r'^([IVAB]+(?:\s*[ABC])?)\s+(\d+)', x.get('textCompilation') or '', re.I)
    if m:
        return 'isbrytarassistans bara för fartyg med isklass %s eller högre och minst %s dwt' % (m.group(1), '{:,}'.format(int(m.group(2))).replace(',', ' '))
    return 'restriktion ' + (x.get('textCompilation') or '')


def n1(x):
    return ('%.1f' % x).replace('.', ',')


def weather_block(w, now):
    rows = []
    if 'now' in w:
        d = w['now']
        rows.append(('Vind i hamnen', '%s m/s från %s, byar %s m/s. %s °C.' % (
            n1(d['wind_speed']), DIRS[round((d['wind_from_direction'] % 360) / 22.5) % 16], n1(d['wind_speed_of_gust']), n1(d['air_temperature']))))
    if 'gust_max' in w:
        t_, d = w['gust_max']
        g = d['wind_speed_of_gust']
        txt = 'Starkaste byarna %s m/s kl. %s.' % (n1(g), t_.astimezone(TZ).strftime('%H:%M'))
        if g >= GUST_HI:
            txt = '<b style="color:%s">%s Kuling eller mer.</b>' % (ORANGE, txt)
        lo, hi = w['temps']
        rows.append(('Kommande dygn', txt + ' Temperatur %s till %s °C.' % (n1(lo), n1(hi))))
    if 'odor' in w:
        if w['odor']:
            hm = lambda x: x.astimezone(TZ).strftime('%H:%M')
            txt = '<b style="color:#B91C1C">Risk för lukt mot samhället Hargshamn.</b> Östlig vind (70–110°) väntas %s.' % ', '.join(
                '%s–%s' % (hm(a), hm(b + timedelta(hours=1))) for a, b in w['odor'])
        else:
            txt = 'Ingen östlig vind mot samhället väntas det kommande dygnet.'
        rows.append(('Lukt', txt))
    if 'sea' in w:
        v, dv = w['sea']
        trend = 'oförändrat' if abs(dv) < 2 else ('stiger' if dv > 0 else 'sjunker')
        rows.append(('Vattenstånd', '%s%d cm mot medelvattenstånd i Forsmark, %s.' % ('+' if v > 0 else '', round(v), trend)))
    if 'ice' in w:
        cur, nxt, p = w['ice']
        txt = ('Restriktion sedan %s: %s.' % (p(cur['startTime']).astimezone(TZ).strftime('%y-%m-%d'), ice_txt(cur))) if cur else 'Inga trafikrestriktioner för Hargshamn.'
        if nxt:
            txt += ' Från %s: %s.' % (p(nxt['startTime']).astimezone(TZ).strftime('%y-%m-%d'), ice_txt(nxt))
        rows.append(('Vintersjöfart', txt))
    if not rows and not w.get('warn'):
        return empty('Väderuppgifterna kunde inte hämtas i morse.')
    out = ''.join(
        '<tr><td valign="top" style="padding:7px 0;border-bottom:1px solid %s;font-size:11px;font-weight:bold;letter-spacing:1px;color:%s;width:110px;font-family:Arial,Helvetica,sans-serif">%s</td>'
        '<td style="padding:7px 0 7px 8px;border-bottom:1px solid %s;font-size:14px;color:%s;font-family:Arial,Helvetica,sans-serif">%s</td></tr>' % (LINE, MUTED, k.upper(), LINE, INK, v)
        for k, v in rows)
    warn = ''.join('<div style="background:#FFF4E0;border-left:4px solid #E8A33D;padding:8px 10px;font-size:13px;margin-top:8px">%s</div>' % x for x in w.get('warn', []))
    return '<div style="background:#ffffff;border-radius:8px;padding:6px 16px 10px"><table role="presentation" width="100%%" cellspacing="0" cellpadding="0">%s</table>%s</div>' % (out, warn)


def lots_for(lots, name):
    n = (name or '').strip().upper()
    return sorted([c for c in lots if c['name'] == n], key=lambda c: c.get('planned') or '')


def kaj_txt(c):
    return 'kaj %d %s' % (c['kaj'], c.get('kajnamn') or '') if c and c.get('kaj') else ''


def status_tag(st):
    # samma färger som märkena på webbsidan
    fg, bg = {'Bekräftad': ('#2E7D32', '#E3F1E4'), 'Pågående': ('#2E7D32', '#E3F1E4'), 'Preliminär': ('#9A5B00', '#FFF4E0')}.get(st, (MUTED, '#F0F4F8'))
    return pill((st or '').upper(), fg, bg)


def pill(txt, fg, bg):
    return ('<span style="display:inline-block;font-size:10px;font-weight:bold;letter-spacing:1px;color:%s;background:%s;'
            'padding:2px 6px;border-radius:3px;white-space:nowrap">%s</span>') % (fg, bg, txt)


def dir_pill(d):
    # samma bredd på IN och UT
    fg, bg, txt = ('#2E7D32', '#E3F1E4', '&#8601;&nbsp;IN') if d == 'in' else ('#0A629E', '#E2EEF7', '&#8599;&nbsp;UT')
    return ('<span style="display:inline-block;width:42px;text-align:center;font-size:10px;font-weight:bold;letter-spacing:1px;color:%s;background:%s;'
            'padding:2px 0;border-radius:3px;white-space:nowrap">%s</span>') % (fg, bg, txt)


def kaj_pin(c, prefix):
    return '%s kaj %d %s' % (prefix, c['kaj'], c.get('kajnamn') or '') if c and c.get('kaj') else ''


def lots_in(inn):
    return ('Lots in beställd <b style="color:%s">%s</b><br>%s' % (NAVY, fmt(inn['planned']), status_tag(inn.get('status')))) if inn else ''


def lots_table(lots, now):
    show = sorted([c for c in lots if c.get('status') != 'Avslutad' and c.get('planned')
                   and now - timedelta(hours=6) < t(c['planned']) < now + timedelta(days=4)], key=lambda c: c['planned'])
    if not show:
        return empty('Inga beställda lotsningar till eller från Hargshamn de närmaste dagarna.')
    return lots_rows(show, False)


def lots_done(lots, now):
    """De fem senast avslutade lotsningarna det senaste dygnet, nyast först."""
    done = [c for c in lots if c.get('status') == 'Avslutad' and (c.get('finished') or c.get('planned'))
            and t(c.get('finished') or c['planned']) > now - timedelta(hours=24)]
    done.sort(key=lambda c: c.get('finished') or c['planned'], reverse=True)
    return lots_rows(done[:5], True) if done else ''


def lots_rows(show, done):
    def when(c):
        return fmt(c.get('finished') or c.get('started') or c['planned']) if done else fmt(c['planned'])

    def sub(c):
        k = kaj_txt(c)
        k = k[:1].upper() + k[1:]
        if done and c.get('started'):
            k = ' · '.join(x for x in (k, 'lots ombord ' + fmt(c['started']) + ('–' + t(c['finished']).astimezone(TZ).strftime('%H:%M') if c.get('finished') else '')) if x)
        return k
    rows = ''.join(
        '<tr><td style="padding:8px 0;border-bottom:1px solid %s;font-size:13px;font-weight:bold;color:%s;white-space:nowrap;font-family:Arial,Helvetica,sans-serif">%s</td>'
        '<td style="padding:8px 6px;border-bottom:1px solid %s;font-size:11px;font-weight:bold;color:%s;font-family:Arial,Helvetica,sans-serif">%s</td>'
        '<td style="padding:8px 0;border-bottom:1px solid %s;font-size:13px;color:%s;font-family:Arial,Helvetica,sans-serif"><b>%s</b><br><span style="color:%s;font-size:12px">%s</span></td>'
        '<td align="right" style="padding:8px 0;border-bottom:1px solid %s">%s</td></tr>' % (
            LINE, NAVY, when(c), LINE, GREEN if c['dir'] == 'in' else BLUE, dir_pill(c['dir']),
            LINE, INK, c['name'], MUTED, sub(c), LINE, status_tag(c.get('status')))
        for c in show)
    th = '<td style="padding:8px 0 6px;border-bottom:2px solid %s;font-size:10px;font-weight:bold;letter-spacing:1px;color:%s;font-family:Arial,Helvetica,sans-serif"%s>%s</td>'
    hdr = '<tr>' + ''.join(th % (NAVY, MUTED, a, x) for x, a in (('AVSLUTAD' if done else 'PLANERAD', ''), ('', ''), ('FARTYG OCH KAJ', ''), ('STATUS', ' align="right"'))) + '</tr>'
    return '<div style="background:#ffffff;border-radius:8px;padding:4px 16px 6px"><table role="presentation" width="100%%" cellspacing="0" cellpadding="0">%s%s</table></div>' % (hdr, rows)


def sanctions_block():
    """Röd ruta överst om sanktionskontrollen (scripts/sanktion.py) har träffar."""
    hits = (logg.load('sanktion.json', {}) or {}).get('hits') or {}
    if not hits:
        return ''
    who = {'EU': 'EU', 'UK': 'Storbritannien', 'US': 'USA (OFAC)'}
    rows = ''.join('<div style="margin-top:4px"><b>%s</b>%s, %s: %s %s</div>' % (
        h['name'], (' (IMO %s)' % h['imo']) if h.get('imo') else '', h.get('var', ''),
        'finns på' if h.get('sure') else 'namnet matchar, IMO saknas, kontrollera:',
        ', '.join(who.get(k, k) + ((' ' + r) if r else '') for k, r in sorted(h['lists'].items())))
        for h in hits.values())
    red = any(h.get('sure') and 'EU' in h['lists'] for h in hits.values())
    return ('<div style="background:%s;color:%s;border-radius:8px;padding:12px 16px;margin:14px 0 0;font-size:13px;line-height:1.5;font-family:Arial,Helvetica,sans-serif">'
            '<div style="font-size:11px;font-weight:bold;letter-spacing:1px">SANKTIONSKONTROLL</div>%s'
            '<div style="margin-top:6px;font-size:12px">Kontrollera uppgifterna mot listan innan beslut.</div></div>') % (
        '#FCEBEB' if red else '#FFF4E0', '#791F1F' if red else '#7A4800', rows)


def build(now, ships, anlop, coming, lots=None, wx=None):
    day = now - timedelta(hours=24)
    quay = [(m, s) for m, s in ships.items() if s.get('arrival') or s.get('before_log')]
    redd = [(m, s) for m, s in ships.items() if s.get('anchor_start') and not s.get('arrival') and not s.get('before_log')]
    lots, wx = lots or [], wx or {}
    known = {(s.get('name') or '').strip().upper() for s in ships.values()} | {n.strip().upper() for _, n, _, _ in coming}
    expected = [c for c in sorted(lots, key=lambda c: c.get('planned') or '')
                if c['dir'] == 'in' and c.get('status') != 'Avslutad' and c['name'] not in known]

    body = sanctions_block() + head('VID KAJ', GREEN)
    for m, s in sorted(quay, key=lambda x: x[1].get('arrival') or ''):
        lab, size = info(m, s.get('shipType'))
        typ_, *rest = lab.split(' · ')
        ls = lots_for(lots, s['name'])
        ut = next((c for c in ls if c['dir'] == 'ut' and c.get('status') != 'Avslutad'), None)
        k = ut or next((c for c in reversed(ls) if c['dir'] == 'in'), None)
        if s.get('before_log') or not s.get('arrival'):
            tid, ank = 'sedan före loggstart', 'Låg vid kaj när loggen startade'
        else:
            w = logg.hours(s.get('anchor_start'), s['arrival'])
            tid = dur((now - t(s['arrival'])).total_seconds() / 3600)
            ank = 'Ankom %s · %s' % (fmt(s['arrival']), ('väntade %s på redden' % dur(w)) if w else 'ingen väntan på redden')
        pin = ' · '.join(x for x in ((kaj_pin(k, 'Förtöjd vid') or 'Förtöjd vid kaj'), tid) if x)
        dest = DESTS.get(str(m), '')
        dep = ('Avgår enligt lotsplanen <b style="color:%s">%s</b>%s<br>%s' % (
            NAVY, fmt(ut['planned']), (' till <b style="color:%s">%s</b>' % (NAVY, cap(dest))) if dest and not DEST.search(dest) else '',
            status_tag(ut.get('status')))) if ut else ''
        body += card(GREEN, s['name'], typ_, pin, ' · '.join(rest + ([size] if size else [])), '', ank + ('<br>' + dep if dep else ''))
    if not quay:
        body += empty('Inget fartyg vid kaj just nu.')

    body += head('PÅ REDDEN', ORANGE)
    for m, s in sorted(redd, key=lambda x: x[1]['anchor_start']):
        lab, size = info(m, s.get('shipType'))
        typ_, *rest = lab.split(' · ')
        h = (now - t(s['anchor_start'])).total_seconds() / 3600
        inn = next((c for c in lots_for(lots, s['name']) if c['dir'] == 'in' and c.get('status') != 'Avslutad'), None)
        dest = DESTS.get(str(m), '')
        sub = ' · '.join(rest + ([size] if size else []) + (['Destination ' + cap(dest)] if dest else []))
        foot = 'Ligger still sedan ' + fmt(s['anchor_start']) + ('<br>' + lots_in(inn) if inn else '')
        body += card(ORANGE, s['name'], typ_, kaj_pin(inn, 'Planerad'), sub, dur(h), foot)
    if not redd:
        body += empty('Inga fartyg väntar på redden.')

    body += head('PÅ VÄG IN', CYAN)
    for e, name, m, st in coming[:6]:
        lab, size = info(m, st)
        typ_, *rest = lab.split(' · ')
        inn = next((c for c in lots_for(lots, name) if c['dir'] == 'in' and c.get('status') != 'Avslutad'), None)
        dest = DESTS.get(str(m), '')
        sub = ' · '.join(rest + ([size] if size else []) + (['Destination ' + cap(dest)] if dest else []))
        right = '<span style="font-size:13px">' + ('ETA ' + e.astimezone(TZ).strftime('%y-%m-%d %H:%M') if e else 'ETA saknas') + '</span>'
        body += card(CYAN, name, typ_, kaj_pin(inn, 'Planerad'), sub, right, lots_in(inn))
    for c in expected:
        body += card(CYAN, c['name'], 'Lotsplan', kaj_pin(c, 'Planerad'), 'Syns inte i AIS-området än', '', lots_in(c))
    if not coming and not expected:
        body += empty('Inget fartyg har angett Hargshamn som destination.')

    body += head('LOTSPLAN', BLUE) + lots_table(lots, now)
    done = lots_done(lots, now)
    if done:
        body += ('<div style="font-size:11px;font-weight:bold;letter-spacing:2px;color:%s;margin:14px 4px 8px">AVSLUTADE SENASTE DYGNET</div>' % MUTED) + done
    body += head('VÄDER OCH VATTEN', CYAN) + weather_block(wx, now)

    ev = []
    for m, s in ships.items():
        if s.get('arrival') and t(s['arrival']) >= day:
            w = logg.hours(s.get('anchor_start'), s['arrival'])
            ev.append((t(s['arrival']), 'in', '<b>%s</b> kom till kaj%s' % (s['name'], ', efter %s på redden' % dur(w) if w else '')))
        if s.get('anchor_start') and t(s['anchor_start']) >= day:
            ev.append((t(s['anchor_start']), 'redd', '<b>%s</b> la sig på redden' % s['name']))
    for a in anlop:
        if t(a['departure']) >= day:
            q = a.get('hours_at_quay')
            ev.append((t(a['departure']), 'ut', '<b>%s</b> lämnade kaj%s' % (a['name'], ' efter %s' % dur(q) if q else '')))
        if a.get('arrival') and t(a['arrival']) >= day:
            ev.append((t(a['arrival']), 'in', '<b>%s</b> kom till kaj' % a['name']))
        if a.get('anchor_start') and t(a['anchor_start']) >= day:
            ev.append((t(a['anchor_start']), 'redd', '<b>%s</b> la sig på redden' % a['name']))
    body += head('SENASTE DYGNET', BLUE) + timeline(ev, now)

    body += ('<div style="margin:20px 0 8px"><a href="%s" style="display:block;background:%s;color:#ffffff;text-decoration:none;text-align:center;'
             'font-weight:bold;font-size:15px;padding:13px;border-radius:6px;font-family:Arial,Helvetica,sans-serif">Öppna kartan och hela listan</a></div>') % (PAGE, NAVY)
    body += ('<div style="font-size:11px;color:%s;padding:8px 4px 4px;line-height:1.5">Källa: Fintraffic, digitraffic.fi, AIS-data under licensen CC BY 4.0. '
             'Lotsningar: Sjöfartsverkets publika lotsplan, planeringsstöd som inte kan läggas till grund för anspråk mot Sjöfartsverket. '
             'Väder och vattenstånd: SMHI. Vintersjöfart: Sjöfartsverket och Fintraffic. '
             'Tiderna är svensk tid.<br><a href="%s" style="color:%s">Avsluta eller ändra prenumerationen</a></div>'
             '<!--ADMIN-->') % (MUTED, FORM_URL, MUTED)

    lede = '%s fartyg vid kaj' % (['Inget', 'Ett', 'Två', 'Tre', 'Fyra', 'Fem', 'Sex'][len(quay)] if len(quay) < 7 else len(quay))
    lede += ', %s väntar på redden.' % (['inget', 'ett', 'två', 'tre', 'fyra', 'fem', 'sex'][len(redd)] if len(redd) < 7 else len(redd)) if redd else '.'
    nxt = sorted([c for c in lots if c.get('status') != 'Avslutad' and c.get('planned') and now < t(c['planned']) < now + timedelta(hours=36)],
                 key=lambda c: c['planned'])
    if nxt:
        c = nxt[0]
        lede += ' Nästa lotsning: %s %s %s.' % (c['name'], 'in' if c['dir'] == 'in' else 'ut', fmt(c['planned']))
    lokal = now.astimezone(TZ)
    datum_txt = '%s %d %s' % (DAGAR[lokal.weekday()], lokal.day, MAN[lokal.month - 1])
    top = ('<tr><td style="background:%s;border-radius:10px 10px 0 0;padding:22px 20px 20px;font-family:Arial,Helvetica,sans-serif">'
           '<div style="font-size:11px;font-weight:bold;letter-spacing:3px;color:%s">HARGS HAMN &middot; MORGONLÄGE</div>'
           '<div style="font-size:26px;font-weight:bold;margin-top:6px;color:#ffffff">%s</div>'
           '<div style="font-size:14px;color:#B9CCE0;margin-top:4px">%s</div></td></tr>') % (NAVY, CYAN, datum_txt, lede)
    top += ('<tr><td style="background:%s;padding:14px 6px 2px"><table role="presentation" width="100%%" cellspacing="0" cellpadding="0"><tr>%s%s%s</tr></table></td></tr>') % (
        BG, stat(len(quay), 'VID KAJ', GREEN), stat(len(redd), 'PÅ REDDEN', ORANGE), stat(len(coming) + len(expected), 'PÅ VÄG', CYAN))
    top += '<tr><td style="background:%s;padding:0 12px 18px;border-radius:0 0 10px 10px;font-family:Arial,Helvetica,sans-serif">%s</td></tr>' % (BG, body)
    html = ('<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="margin:0;padding:0;background:#E9EEF3"><table role="presentation" width="100%%" cellspacing="0" cellpadding="0" style="background:#E9EEF3">'
            '<tr><td align="center" style="padding:16px 10px"><table role="presentation" width="100%%" cellspacing="0" cellpadding="0" '
            'style="max-width:600px;font-family:Arial,Helvetica,sans-serif;color:%s">%s</table></td></tr></table></body></html>') % (INK, top)
    text = 'Fartyg i Hargshamn, %s\n%s\nVid kaj: %s\nPå redden: %s\nSe %s' % (
        datum_txt, lede, ', '.join(s['name'] for _, s in quay) or 'inga', ', '.join(s['name'] for _, s in redd) or 'inga', PAGE)
    return html, text


def main():
    need = ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'MAIL_FROM']
    if not all(os.environ.get(k) for k in need):
        print('Inställningar saknas, inget skickas.')
        return
    fixed = sorted({m.strip().lower() for m in (os.environ.get('MAIL_TO') or '').split(',') if m.strip()})
    subs = [m for m in subscribers() if m not in fixed]
    to = fixed + subs
    if not to:
        print('Inga mottagare.')
        return
    now = datetime.now(timezone.utc)
    state = logg.load('state.json', {'ships': {}})
    ships = state.get('ships', {})
    html, text = build(now, ships, logg.load('anlop.json', []), on_the_way(ships, now),
                       logg.load('lots.json', {}).get('calls', []), weather(now))
    datum = now.astimezone(TZ).strftime('%Y-%m-%d')

    # de fasta mottagarna (MAIL_TO) får också en rad om vilka som får mejlet
    admin = ('<p style="font-size:11px;color:#52606D;border-top:1px solid #D9E2EC;padding:8px 4px 0;margin:8px 0 0">'
             'Bara till dig: i dag skickat till %d mottagare: %s</p>') % (len(to), ', '.join(to))
    msgs = []
    for rcpt, body in ((fixed, html.replace('<!--ADMIN-->', admin)), (subs, html)):
        if not rcpt:
            continue
        msg = EmailMessage()
        msg['Subject'] = 'Fartyg i Hargshamn %s' % datum
        msg['From'] = os.environ['MAIL_FROM']
        msg['To'] = os.environ['MAIL_FROM']
        msg['Bcc'] = ', '.join(rcpt)
        msg.set_content(text)
        msg.add_alternative(body, subtype='html')
        msgs.append(msg)
    port = int(os.environ['SMTP_PORT'])
    if port == 465:
        with smtplib.SMTP_SSL(os.environ['SMTP_HOST'], port, context=ssl.create_default_context()) as s:
            s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
            for m in msgs: s.send_message(m)
    else:
        with smtplib.SMTP(os.environ['SMTP_HOST'], port) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
            for m in msgs: s.send_message(m)
    print('Skickat till %d mottagare.' % len(to))


if __name__ == '__main__':
    main()
