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
    return d.astimezone(TZ).strftime('%-d/%-m %H:%M') if d else ''


def dur(h):
    if h is None:
        return ''
    return ('%.1f tim' % h if h < 48 else '%.1f dygn' % (h / 24)).replace('.', ',')


NAVY, CYAN, BLUE, INK, MUTED, LINE, BG = '#0B1F33', '#2BA8E0', '#0A629E', '#1F2933', '#52606D', '#D9E2EC', '#F4F7FA'
GREEN, ORANGE = '#2E7D32', '#C2410C'
DEST = re.compile(r'HARG|SEHAN', re.I)
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
    return 'Last' if 70 <= st < 80 else 'Tank' if 80 <= st < 90 else 'Fartyg'


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


def card(label, c, name, left1, left2, right, foot='', prog=None):
    bar = ''
    if prog is not None:
        bar = ('<table role="presentation" width="100%%" cellspacing="0" cellpadding="0" style="margin-top:10px"><tr>'
               '<td width="%d%%" style="background:%s;height:6px;font-size:0;line-height:0;border-radius:3px 0 0 3px">&nbsp;</td>'
               '<td style="background:%s;height:6px;font-size:0;line-height:0">&nbsp;</td></tr></table>') % (max(3, min(100, int(prog * 100))), c, LINE)
    if foot:
        bar += '<div style="font-size:12px;color:%s;margin-top:5px">%s</div>' % (MUTED, foot)
    return ('<div style="background:#ffffff;border-radius:8px;padding:14px 16px;margin:0 0 10px">'
            '<div style="font-size:11px;font-weight:bold;letter-spacing:1px;color:%s">%s</div>'
            '<div style="font-size:19px;font-weight:bold;color:%s;margin:2px 0 6px">%s</div>'
            '<table role="presentation" width="100%%" cellspacing="0" cellpadding="0"><tr>'
            '<td style="font-size:13px;color:%s;font-family:Arial,Helvetica,sans-serif">%s<br><span style="color:%s">%s</span></td>'
            '<td align="right" valign="middle" style="font-size:20px;font-weight:bold;color:%s;white-space:nowrap;font-family:Arial,Helvetica,sans-serif">%s</td>'
            '</tr></table>%s</div>') % (c, label.upper(), NAVY, name, INK, left1, MUTED, left2, NAVY, right, bar)


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


def build(now, ships, anlop, coming):
    day = now - timedelta(hours=24)
    quay = [(m, s) for m, s in ships.items() if s.get('arrival') or s.get('before_log')]
    redd = [(m, s) for m, s in ships.items() if s.get('anchor_start') and not s.get('arrival') and not s.get('before_log')]

    body = head('VID KAJ', GREEN)
    for m, s in sorted(quay, key=lambda x: x[1].get('arrival') or ''):
        lab, size = info(m, s.get('shipType'))
        if s.get('before_log') or not s.get('arrival'):
            body += card(lab, GREEN, s['name'], 'Låg vid kaj när loggen startade', size, '&ndash;')
        else:
            h = (now - t(s['arrival'])).total_seconds() / 3600
            w = logg.hours(s.get('anchor_start'), s['arrival'])
            foot = 'Väntade %s på redden' % dur(w) if w else 'Ingen väntan på redden'
            body += card(lab, GREEN, s['name'], 'Ankom ' + fmt(s['arrival']), size, dur(h), foot, h / 120)
    if not quay:
        body += empty('Inget fartyg vid kaj just nu.')

    body += head('PÅ REDDEN', ORANGE)
    for m, s in sorted(redd, key=lambda x: x[1]['anchor_start']):
        lab, size = info(m, s.get('shipType'))
        h = (now - t(s['anchor_start'])).total_seconds() / 3600
        body += card(lab, ORANGE, s['name'], 'Ligger still sedan ' + fmt(s['anchor_start']), size, dur(h))
    if not redd:
        body += empty('Inga fartyg väntar på redden.')

    body += head('PÅ VÄG IN', CYAN)
    for e, name, m, st in coming[:6]:
        lab, size = info(m, st)
        body += card(lab, CYAN, name, 'Enligt fartygets egen uppgift', size,
                     '<span style="font-size:14px">' + ('ETA ' + e.astimezone(TZ).strftime('%-d/%-m %H:%M') if e else 'ETA saknas') + '</span>')
    if not coming:
        body += empty('Inget fartyg har angett Hargshamn som destination.')

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
             'Tiderna är svensk tid och bygger på fartygens AIS-sändare.<br><a href="%s" style="color:%s">Avsluta eller ändra prenumerationen</a></div>'
             '<!--ADMIN-->') % (MUTED, FORM_URL, MUTED)

    lede = '%s fartyg vid kaj' % (['Inget', 'Ett', 'Två', 'Tre', 'Fyra', 'Fem', 'Sex'][len(quay)] if len(quay) < 7 else len(quay))
    lede += ', %s väntar på redden.' % (['inget', 'ett', 'två', 'tre', 'fyra', 'fem', 'sex'][len(redd)] if len(redd) < 7 else len(redd)) if redd else '.'
    lokal = now.astimezone(TZ)
    datum_txt = '%s %d %s' % (DAGAR[lokal.weekday()], lokal.day, MAN[lokal.month - 1])
    top = ('<tr><td style="background:%s;border-radius:10px 10px 0 0;padding:22px 20px 20px;font-family:Arial,Helvetica,sans-serif">'
           '<div style="font-size:11px;font-weight:bold;letter-spacing:3px;color:%s">HARGS HAMN &middot; MORGONLÄGE</div>'
           '<div style="font-size:26px;font-weight:bold;margin-top:6px;color:#ffffff">%s</div>'
           '<div style="font-size:14px;color:#B9CCE0;margin-top:4px">%s</div></td></tr>') % (NAVY, CYAN, datum_txt, lede)
    top += ('<tr><td style="background:%s;padding:14px 6px 2px"><table role="presentation" width="100%%" cellspacing="0" cellpadding="0"><tr>%s%s%s</tr></table></td></tr>') % (
        BG, stat(len(quay), 'VID KAJ', GREEN), stat(len(redd), 'PÅ REDDEN', ORANGE), stat(len(coming), 'PÅ VÄG', CYAN))
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
    html, text = build(now, ships, logg.load('anlop.json', []), on_the_way(ships, now))
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
