"""
CORS-Proxy für SuperSaaS API + E-Mail-Versand bei Kontaktanfragen.
Startet auf Port 8081.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request
import urllib.error
import smtplib
import json
import os

# .env laden
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ[key.strip()] = val.strip()

load_env()

SUPERSAAS_ACCOUNT = 'Hupfgaudi-Vilshofen'
SUPERSAAS_API_KEY = 'VClzqXLcmH6QLYs2Ksr_8A'
SCHEDULE_ID = '795126'

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
SMTP_USER = os.environ.get('SMTP_USER', 'hupfgaudi@gmail.com')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
EMAIL_TO = os.environ.get('EMAIL_TO', 'buchung@hupfgaudi-vilshofen.de,gutse@gmx.de')


class ProxyHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith('/api/bookings'):
            self._proxy_bookings()
        elif self.path.startswith('/api/free'):
            self._proxy_free()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/send-email'):
            self._send_email()
        elif self.path.startswith('/api/create-booking'):
            self._create_booking()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    # ── SuperSaaS Proxy ──

    def _proxy_bookings(self):
        params = parse_qs(urlparse(self.path).query)
        from_date = params.get('from', [''])[0]
        to_date = params.get('to', [''])[0]
        url = (f'https://www.supersaas.com/api/bookings.json'
               f'?schedule_id={SCHEDULE_ID}'
               f'&account={SUPERSAAS_ACCOUNT}'
               f'&api_key={SUPERSAAS_API_KEY}'
               f'&from={from_date}&to={to_date}')
        self._fetch_and_respond(url)

    def _proxy_free(self):
        params = parse_qs(urlparse(self.path).query)
        from_date = params.get('from', [''])[0]
        url = (f'https://www.supersaas.com/api/free/{SCHEDULE_ID}.json'
               f'?account={SUPERSAAS_ACCOUNT}'
               f'&api_key={SUPERSAAS_API_KEY}'
               f'&from={from_date}')
        self._fetch_and_respond(url)

    def _fetch_and_respond(self, url):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as response:
                data = response.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── E-Mail-Versand ──

    def _send_email(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            name       = data.get('name', '')
            email      = data.get('email', '')
            telefon    = data.get('telefon', '')
            datum      = data.get('datum', '')
            burg       = data.get('burg', '')
            lieferung  = data.get('lieferung', '')
            extras     = data.get('extras', [])
            untergrund = data.get('untergrund', '')
            nachricht  = data.get('nachricht', '')

            # HTML-E-Mail erstellen
            html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #ff5a1f, #ffcc00); padding: 20px; border-radius: 12px 12px 0 0; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 1.4rem;">🏰 Neue Anfrage bei HupfGaudi!</h1>
                </div>
                <div style="background: #f9f9f9; padding: 24px; border-radius: 0 0 12px 12px; border: 1px solid #eee;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 10px 0; font-weight: bold; color: #555; width: 140px;">👤 Name:</td>
                            <td style="padding: 10px 0; color: #333;">{name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 10px 0; font-weight: bold; color: #555;">✉️ E-Mail:</td>
                            <td style="padding: 10px 0;"><a href="mailto:{email}" style="color: #ff5a1f;">{email}</a></td>
                        </tr>
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">📱 Telefon:</td><td style="padding: 10px 0;"><a href="tel:' + telefon + '" style="color: #ff5a1f;">' + telefon + '</a></td></tr>' if telefon else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">📅 Wunschdatum:</td><td style="padding: 10px 0; color: #333;">' + datum + '</td></tr>' if datum else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🏰 Hüpfburg:</td><td style="padding: 10px 0; color: #333; font-weight: bold;">' + burg + '</td></tr>' if burg else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🚗 Abholung/Lieferung:</td><td style="padding: 10px 0; color: #333;">' + lieferung + '</td></tr>' if lieferung else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🎉 Partyzubehör:</td><td style="padding: 10px 0; color: #333;">' + '<br>'.join('• ' + e for e in extras) + '</td></tr>' if extras else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">📍 Aufstellplatz:</td><td style="padding: 10px 0; color: #333;">' + untergrund + '</td></tr>' if untergrund else ''}
                    </table>
                    {'<div style="margin-top: 16px; padding: 14px; background: #fff; border-radius: 8px; border-left: 4px solid #ff5a1f;"><strong style="color: #555;">💬 Nachricht:</strong><p style="margin: 8px 0 0; color: #333;">' + nachricht.replace(chr(10), '<br>') + '</p></div>' if nachricht else ''}
                    <div style="margin-top: 20px; padding: 12px; background: #fff8e7; border-radius: 8px; text-align: center; font-size: 0.85rem; color: #888;">
                        Diese Anfrage wurde über das Kontaktformular auf hupfgaudi-vilshofen.de gesendet.
                    </div>
                </div>
            </body>
            </html>
            """

            # E-Mail zusammenbauen
            subject = f'🏰 Neue Anfrage: {burg or "Allgemeine Anfrage"} – {name}'

            recipients = [addr.strip() for addr in EMAIL_TO.split(',')]

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f'HupfGaudi Vilshofen <{SMTP_USER}>'
            msg['To'] = ', '.join(recipients)
            msg['Reply-To'] = email

            # Klartext-Version
            text = f"Neue Anfrage von {name}\n"
            text += f"E-Mail: {email}\n"
            if telefon: text += f"Telefon: {telefon}\n"
            if datum: text += f"Datum: {datum}\n"
            if burg: text += f"Hüpfburg: {burg}\n"
            if lieferung: text += f"Abholung/Lieferung: {lieferung}\n"
            if extras: text += f"Partyzubehör: {', '.join(extras)}\n"
            if untergrund: text += f"Aufstellplatz: {untergrund}\n"
            if nachricht: text += f"\nNachricht:\n{nachricht}\n"

            msg.attach(MIMEText(text, 'plain', 'utf-8'))
            msg.attach(MIMEText(html, 'html', 'utf-8'))

            # Senden
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, recipients, msg.as_string())

            print(f'E-Mail gesendet an: {", ".join(recipients)} - Anfrage von {name}')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())

        except Exception as e:
            print(f'E-Mail-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── SuperSaaS Buchung anlegen ──

    RESOURCE_MAP = {
        'Ritterburg Hopsala': 1151497,
        'Alien': 1177924,
        'Einhorn': 1151496,
        'Einhorn Rosalie': 1151496,
        'Feuerwehr': 1151498,
        'Fußballstar': 1151499,
        'Meerjungfrau': 1177923,
        'Hochzeit': 1177925,
        'Hochzeitsburg': 1177925,
        'Popcornmaschine': 1205013,
        'Popcorn': 1205013,
        'Zuckerwattemaschine': 1205014,
        'Zuckerwattenmaschine': 1205014,
        'Zuckerwatte': 1205014,
        'Icecream Roll': 1205015,
        'Ice Cream Roll Maschine': 1205015,
        'Ice Cream Roll': 1205015,
    }

    def _find_resource_id(self, text):
        """Findet die resource_id anhand eines Textfragments."""
        text_lower = text.lower()
        for name, rid in self.RESOURCE_MAP.items():
            if name.lower() in text_lower:
                return rid
        return None

    def _create_supersaas_booking(self, resource_id, name, email, telefon, datum, untergrund, info):
        """Erstellt eine einzelne Buchung in SuperSaaS."""
        start = f'{datum} 08:00:00'
        finish = f'{datum} 19:00:00'

        payload = json.dumps({
            'booking': {
                'start': start,
                'finish': finish,
                'resource_id': resource_id,
                'full_name': name,
                'email': email,
                'mobile': telefon,
                'address': untergrund or 'Nicht angegeben',
                'field_1_r': untergrund,
                'field_2_r': info,
            }
        }).encode('utf-8')

        url = (f'https://www.supersaas.com/api/bookings.json'
               f'?schedule_id={SCHEDULE_ID}'
               f'&account={SUPERSAAS_ACCOUNT}'
               f'&api_key={SUPERSAAS_API_KEY}')

        req = urllib.request.Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req) as response:
            return response.read()

    def _create_booking(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            name       = data.get('name', '')
            email      = data.get('email', '')
            telefon    = data.get('telefon', '')
            datum      = data.get('datum', '')
            burg       = data.get('burg', '')
            lieferung  = data.get('lieferung', '')
            extras     = data.get('extras', [])
            untergrund = data.get('untergrund', '')
            nachricht  = data.get('nachricht', '')

            if not datum:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Kein Datum angegeben'}).encode())
                return

            results = []

            # Hüpfburg-Buchung anlegen
            if burg:
                resource_id = self._find_resource_id(burg)
                if resource_id:
                    info = f'{lieferung or "Selbstabholung"}'
                    if nachricht:
                        info += f' | {nachricht}'
                    try:
                        self._create_supersaas_booking(
                            resource_id, name, email, telefon, datum, untergrund, info
                        )
                        results.append(f'Huepfburg gebucht: {burg}')
                        print(f'SuperSaaS Buchung: {burg} am {datum} fuer {name}')
                    except urllib.error.HTTPError as e:
                        err_body = e.read().decode('utf-8', errors='replace')
                        results.append(f'Huepfburg-Fehler: {e} - {err_body}')
                        print(f'SuperSaaS Fehler (Huepfburg): {e} - {err_body}')
                    except Exception as e:
                        results.append(f'Huepfburg-Fehler: {e}')
                        print(f'SuperSaaS Fehler (Huepfburg): {e}')

            # Extras buchen (Popcorn, Zuckerwatte, Icecream Roll)
            for extra in extras:
                resource_id = self._find_resource_id(extra)
                if resource_id:
                    try:
                        self._create_supersaas_booking(
                            resource_id, name, email, telefon, datum, untergrund, extra
                        )
                        results.append(f'Extra gebucht: {extra}')
                        print(f'SuperSaaS Buchung: {extra} am {datum} fuer {name}')
                    except Exception as e:
                        results.append(f'Extra-Fehler: {e}')
                        print(f'SuperSaaS Fehler (Extra): {e}')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'results': results}).encode())

        except Exception as e:
            print(f'Booking-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f'E-Mail-Empfaenger: {EMAIL_TO}')
    print(f'SMTP: {SMTP_USER} via {SMTP_HOST}:{SMTP_PORT}')
    server = HTTPServer(('localhost', 8081), ProxyHandler)
    print('Proxy-Server laeuft auf http://localhost:8081')
    server.serve_forever()
