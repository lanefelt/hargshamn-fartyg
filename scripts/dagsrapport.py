"""Daglig fartygsrapport för Hargshamn via e-post.

Läser loggen i data/ och aktuella AIS-positioner och skickar en sammanfattning.
Inställningarna ligger som hemliga värden i repot (Settings > Secrets and variables > Actions):
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM och JOTFORM_API_KEY.
MAIL_TO (valfritt) är fasta mottagare, flera adresser separeras med komma.
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
PAGE = 'https://lanefelt.github.io/hargshamn-fartyg/beta/'
FORM_ID = '263273393605056'
FORM_URL = 'https://form.jotform.com/' + FORM_ID
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


def rows(items, cols):
    if not items:
        return '<p style="color:#52606D;font-size:13px;margin:4px 0 14px">Inga.</p>'
    th = ''.join('<th style="background:#0B1F33;color:#fff;text-align:left;font-size:11px;padding:6px 8px">%s</th>' % c for c in cols)
    tr = ''.join('<tr>' + ''.join('<td style="padding:6px 8px;border-bottom:1px solid #D9E2EC;font-size:13px">%s</td>' % c for c in r) + '</tr>' for r in items)
    return '<table cellspacing="0" style="border-collapse:collapse;width:100%%;margin:4px 0 16px">%s%s</table>' % ('<tr>' + th + '</tr>', tr)


def main():
    need = ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'MAIL_FROM']
    if not all(os.environ.get(k) for k in need):
        print('Inställningar saknas, inget skickas.')
        return
    fixed = [m.strip().lower() for m in (os.environ.get('MAIL_TO') or '').split(',') if m.strip()]
    to = sorted(set(fixed) | set(subscribers()))
    if not to:
        print('Inga mottagare.')
        return
    now = datetime.now(timezone.utc)
    state = logg.load('state.json', {'ships': {}})
    anlop = logg.load('anlop.json', [])
    ships = state.get('ships', {})

    quay = [[s['name'], 'Före loggstart' if s.get('before_log') else fmt(s.get('arrival')),
             '' if s.get('before_log') or not s.get('arrival') else dur((now - t(s['arrival'])).total_seconds() / 3600)]
            for s in ships.values() if s.get('arrival') or s.get('before_log')]
    wait = [[s['name'], fmt(s['anchor_start']), dur((now - t(s['anchor_start'])).total_seconds() / 3600)]
            for s in ships.values() if s.get('anchor_start') and not s.get('arrival') and not s.get('before_log')]
    day = now - timedelta(hours=24)
    left = [[a['name'], 'Före loggstart' if a.get('before_log') else fmt(a.get('arrival')), fmt(a['departure']),
             dur(a.get('hours_at_quay')), dur(a.get('hours_waiting')) if a.get('hours_waiting') else '']
            for a in anlop if t(a['departure']) >= day]
    arrived = [[s['name'], fmt(s['arrival'])] for s in ships.values() if s.get('arrival') and t(s['arrival']) >= day]
    arrived += [[a['name'], fmt(a['arrival'])] for a in anlop if a.get('arrival') and t(a['arrival']) >= day]

    datum = now.astimezone(TZ).strftime('%Y-%m-%d')
    html = ('<html><head><meta charset="utf-8"></head><body><div style="font-family:Arial,Helvetica,sans-serif;color:#1F2933;max-width:640px">'
            '<div style="background:#0B1F33;color:#fff;padding:14px 16px"><div style="font-size:11px;letter-spacing:2px;color:#2BA8E0;font-weight:bold">HARGS HAMN KMA</div>'
            '<div style="font-size:20px;font-weight:bold">Fartyg i Hargshamn %s</div></div><div style="padding:12px 4px">'
            '<h3 style="color:#0A629E;font-size:13px;margin:10px 0 4px">VID KAJ NU</h3>%s'
            '<h3 style="color:#0A629E;font-size:13px;margin:10px 0 4px">LIGGER STILL I NÄRHETEN, TILL EXEMPEL PÅ REDDEN</h3>%s'
            '<h3 style="color:#0A629E;font-size:13px;margin:10px 0 4px">ANKOMSTER SENASTE DYGNET</h3>%s'
            '<h3 style="color:#0A629E;font-size:13px;margin:10px 0 4px">AVGÅNGAR SENASTE DYGNET</h3>%s'
            '<p style="font-size:13px"><a href="%s" style="color:#0A629E">Se kartan och hela listan</a></p>'
            '<p style="font-size:11px;color:#52606D">Källa: Fintraffic, digitraffic.fi, AIS-data under licensen CC BY 4.0. Tiderna är svensk tid och bygger på fartygens AIS-sändare.</p>'
            '<p style="font-size:11px;color:#52606D"><a href="%s" style="color:#52606D">Avsluta eller ändra prenumerationen</a></p>'
            '</div></div></body></html>') % (datum, rows(quay, ['Fartyg', 'Ankom', 'Vid kaj']), rows(wait, ['Fartyg', 'Ligger still sedan', 'Tid']),
                               rows(arrived, ['Fartyg', 'Ankom']), rows(left, ['Fartyg', 'Ankom', 'Avgick', 'Vid kaj', 'Väntan på redden']), PAGE, FORM_URL)
    text = 'Fartyg i Hargshamn %s\nVid kaj: %s\nSe %s' % (datum, ', '.join(r[0] for r in quay) or 'inga', PAGE)

    msg = EmailMessage()
    msg['Subject'] = 'Fartyg i Hargshamn %s' % datum
    msg['From'] = os.environ['MAIL_FROM']
    msg['To'] = os.environ['MAIL_FROM']
    msg['Bcc'] = ', '.join(to)
    msg.set_content(text)
    msg.add_alternative(html, subtype='html')
    port = int(os.environ['SMTP_PORT'])
    if port == 465:
        with smtplib.SMTP_SSL(os.environ['SMTP_HOST'], port, context=ssl.create_default_context()) as s:
            s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS']); s.send_message(msg)
    else:
        with smtplib.SMTP(os.environ['SMTP_HOST'], port) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS']); s.send_message(msg)
    print('Skickat till %d mottagare.' % len(to))


if __name__ == '__main__':
    main()
