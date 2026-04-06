"""
CORS-Proxy für SuperSaaS API + E-Mail-Versand bei Kontaktanfragen.
Startet auf Port 8081.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, quote
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request
import urllib.error
import smtplib
import json
import os
import re
import base64
import uuid

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRICES_FILE = os.path.join(BASE_DIR, 'prices.json')
PRODUCTS_FILE = os.path.join(BASE_DIR, 'products.json')
IMG_DIR = os.path.join(BASE_DIR, 'img')
ANFRAGEN_FILE = os.path.join(BASE_DIR, 'anfragen.json')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
MIETVERTRAG_FILE = os.path.join(BASE_DIR, 'mietvertrag-partyzubehoer.html')
MIETVERTRAG_HB_FILE = os.path.join(BASE_DIR, 'mietvertrag-huepfburg.html')
CONFIRMED_FILE = os.path.join(BASE_DIR, 'confirmed_bookings.json')
CONTRACTS_FILE = os.path.join(BASE_DIR, 'contracts.json')

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
        elif self.path.startswith('/api/prices'):
            self._get_prices()
        elif self.path.startswith('/api/products'):
            self._get_products()
        elif self.path.startswith('/api/anfragen'):
            self._get_anfragen()
        elif self.path.startswith('/api/settings'):
            self._get_settings()
        elif self.path.startswith('/api/confirmed-bookings'):
            self._get_confirmed_bookings()
        elif self.path.startswith('/api/contracts'):
            self._get_contracts()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/send-email'):
            self._send_email()
        elif self.path.startswith('/api/create-booking'):
            self._create_booking()
        elif self.path.startswith('/api/prices'):
            self._save_prices()
        elif self.path.startswith('/api/products'):
            self._save_products()
        elif self.path.startswith('/api/upload-image'):
            self._upload_image()
        elif self.path.startswith('/api/settings'):
            self._save_settings()
        elif self.path.startswith('/api/confirm-booking'):
            self._confirm_booking()
        elif self.path.startswith('/api/submit-contract'):
            self._submit_contract()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
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
            with urllib.request.urlopen(req, timeout=5) as response:
                data = response.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
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

            # Anfrage in anfragen.json speichern
            from datetime import datetime
            anfrage = {
                'id': int(datetime.now().timestamp() * 1000),
                'timestamp': datetime.now().isoformat(),
                'name': name,
                'email': email,
                'telefon': telefon,
                'datum': datum,
                'burg': burg,
                'lieferung': lieferung,
                'extras': extras,
                'untergrund': untergrund,
                'nachricht': nachricht
            }
            try:
                with open(ANFRAGEN_FILE, 'r', encoding='utf-8') as f:
                    anfragen = json.load(f)
            except Exception:
                anfragen = []
            anfragen.insert(0, anfrage)
            with open(ANFRAGEN_FILE, 'w', encoding='utf-8') as f:
                json.dump(anfragen, f, ensure_ascii=False, indent=2)
            print(f'Anfrage gespeichert: {name}')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())

        except Exception as e:
            print(f'E-Mail-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
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
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'results': results}).encode())

        except Exception as e:
            print(f'Booking-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Preise lesen/speichern ──

    def _get_prices(self):
        try:
            with open(PRICES_FILE, 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _save_prices(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            prices = json.loads(body)
            with open(PRICES_FILE, 'w', encoding='utf-8') as f:
                json.dump(prices, f, ensure_ascii=False, indent=2)
            print(f'Preise aktualisiert: {PRICES_FILE}')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            print(f'Preis-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Anfragen lesen ──

    def _get_anfragen(self):
        try:
            with open(ANFRAGEN_FILE, 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception as e:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')

    # ── Settings lesen/speichern ──

    def _get_settings(self):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{}')

    def _save_settings(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            settings = json.loads(body)
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            print(f'Settings aktualisiert')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Produkte lesen/speichern ──

    def _get_products(self):
        try:
            with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Verträge ──

    def _get_contracts(self):
        try:
            with open(CONTRACTS_FILE, 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')

    def _submit_contract(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            from datetime import datetime
            data['id'] = int(datetime.now().timestamp() * 1000)
            data['submitted_at'] = datetime.now().isoformat()

            try:
                with open(CONTRACTS_FILE, 'r', encoding='utf-8') as f:
                    contracts = json.load(f)
            except Exception:
                contracts = []

            contracts.insert(0, data)
            with open(CONTRACTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(contracts, f, ensure_ascii=False, indent=2)

            print(f'Vertrag eingegangen: {data.get("mieter_name", "?")} - {data.get("contract_type", "?")}')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'id': data['id']}).encode())
        except Exception as e:
            print(f'Vertrag-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Buchungsbestätigung an Kunden ──

    def _get_confirmed_bookings(self):
        try:
            with open(CONFIRMED_FILE, 'r', encoding='utf-8') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        except Exception:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')

    def _confirm_booking(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            booking_id = data.get('id')
            name = data.get('full_name', '')
            email = data.get('email', '')
            product_name = data.get('res_name', '')
            product_type = data.get('product_type', 'hupfburg')
            start_date = data.get('start', '').split('T')[0] if data.get('start') else ''
            end_date = data.get('finish', '').split('T')[0] if data.get('finish') else ''
            revenue = data.get('revenue', 0)
            delivery_info = data.get('delivery_info', '')

            if not email:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'no_email'}).encode())
                return

            # Prüfe ob schon bestätigt
            try:
                with open(CONFIRMED_FILE, 'r', encoding='utf-8') as f:
                    confirmed = json.load(f)
            except Exception:
                confirmed = []

            if any(c.get('id') == booking_id for c in confirmed):
                self.send_response(409)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'already_confirmed'}).encode())
                return

            # Datum formatieren
            def fmt_date(d):
                if not d: return '—'
                try:
                    from datetime import datetime
                    return datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m.%Y')
                except Exception:
                    return d

            date_display = fmt_date(start_date)
            if end_date and end_date != start_date:
                date_display += ' – ' + fmt_date(end_date)

            # Mietvertrag-Link
            base_url = 'https://www.hupfgaudi-vilshofen.de'
            if product_type == 'equipment':
                vertrag_url = f'{base_url}/mietvertrag-partyzubehoer.html'
                vertrag_name = 'Mietvertrag Partyzubehör'
            else:
                vertrag_url = f'{base_url}/mietvertrag-huepfburg.html'
                vertrag_name = 'Mietvertrag Hüpfburg'

            # Preis formatieren
            price_text = f'{revenue:.0f} €' if revenue else '—'

            # HTML E-Mail
            html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #ff5a1f, #ffcc00); padding: 24px; border-radius: 12px 12px 0 0; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 1.4rem;">🎉 Deine Buchung ist bestätigt!</h1>
                    <p style="color: rgba(255,255,255,0.9); margin: 6px 0 0; font-size: 0.95rem;">HupfGaudi Vilshofen</p>
                </div>
                <div style="background: #f9f9f9; padding: 24px; border-radius: 0 0 12px 12px; border: 1px solid #eee;">
                    <p style="font-size: 1rem; color: #333;">Hallo {name},</p>
                    <p style="font-size: 0.95rem; color: #555;">vielen Dank für deine Buchung! Hier die Details:</p>

                    <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 10px 0; font-weight: bold; color: #555; width: 140px;">🏰 Produkt:</td>
                            <td style="padding: 10px 0; color: #333; font-weight: bold;">{product_name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 10px 0; font-weight: bold; color: #555;">📅 Datum:</td>
                            <td style="padding: 10px 0; color: #333;">{date_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 10px 0; font-weight: bold; color: #555;">💶 Mietpreis:</td>
                            <td style="padding: 10px 0; color: #333; font-weight: bold;">{price_text}</td>
                        </tr>
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🚗 Abholung/Lieferung:</td><td style="padding: 10px 0; color: #333;">' + delivery_info + '</td></tr>' if delivery_info else ''}
                    </table>

                    <div style="background: #fff8e7; border-radius: 10px; padding: 16px; margin: 20px 0; border: 1px solid #ffcc00;">
                        <p style="margin: 0 0 6px; font-weight: bold; color: #333;">📍 Abholadresse:</p>
                        <p style="margin: 0; color: #555;">Böcklbacher Str. 7, 94474 Vilshofen (Alkofen)</p>
                        <p style="margin: 8px 0 0; font-size: 0.85rem; color: #888;">Bitte bringe den ausgefüllten Mietvertrag und deinen Personalausweis mit.</p>
                    </div>

                    <div style="text-align: center; margin: 24px 0;">
                        <a href="{vertrag_url}" style="display: inline-block; background: #ff5a1f; color: white; text-decoration: none; padding: 14px 32px; border-radius: 10px; font-weight: bold; font-size: 1rem;">✏️ {vertrag_name} online ausfüllen</a>
                        <p style="margin-top: 10px; font-size: 0.85rem; color: #888;">Du kannst den Vertrag digital ausfüllen und absenden – oder ausdrucken und zur Abholung mitbringen.</p>
                    </div>

                    <div style="background: #f0f0f0; border-radius: 8px; padding: 14px; margin-top: 20px;">
                        <p style="margin: 0; font-size: 0.9rem; color: #555;"><strong>Kontakt:</strong></p>
                        <p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">📱 <a href="tel:+4915128861367" style="color: #ff5a1f;">0151 / 28861367</a></p>
                        <p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">✉️ <a href="mailto:hupfgaudi@gmail.com" style="color: #ff5a1f;">hupfgaudi@gmail.com</a></p>
                        <p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">💬 <a href="https://wa.me/4915128861367" style="color: #ff5a1f;">WhatsApp</a></p>
                    </div>

                    <p style="margin-top: 20px; font-size: 0.9rem; color: #555;">Wir freuen uns auf dich! 🎈</p>
                    <p style="font-size: 0.9rem; color: #555;">Dein HupfGaudi Team</p>
                </div>
            </body>
            </html>
            """

            # Klartext
            text = f"Hallo {name},\\n\\n"
            text += f"deine Buchung bei HupfGaudi Vilshofen ist bestätigt!\\n\\n"
            text += f"Produkt: {product_name}\\n"
            text += f"Datum: {date_display}\\n"
            text += f"Preis: {price_text}\\n"
            if delivery_info:
                text += f"Abholung/Lieferung: {delivery_info}\\n"
            text += f"\\nAbholadresse: Böcklbacher Str. 7, 94474 Vilshofen (Alkofen)\\n"
            text += f"Mietvertrag: {vertrag_url}\\n"
            text += f"\\nBei Fragen: 0151/28861367 oder hupfgaudi@gmail.com\\n"

            # E-Mail zusammenbauen
            subject = f'Buchungsbestätigung: {product_name} am {fmt_date(start_date)} – HupfGaudi Vilshofen'

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f'HupfGaudi Vilshofen <{SMTP_USER}>'
            msg['To'] = email
            msg['Reply-To'] = SMTP_USER

            msg.attach(MIMEText(text, 'plain', 'utf-8'))
            msg.attach(MIMEText(html, 'html', 'utf-8'))

            # Senden
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, [email], msg.as_string())

            print(f'Bestätigung gesendet an: {email} - {product_name} am {date_display}')

            # Als bestätigt speichern
            from datetime import datetime
            confirmed.append({
                'id': booking_id,
                'email': email,
                'timestamp': datetime.now().isoformat()
            })
            with open(CONFIRMED_FILE, 'w', encoding='utf-8') as f:
                json.dump(confirmed, f, ensure_ascii=False, indent=2)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())

        except Exception as e:
            print(f'Bestätigung-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _update_mietvertrag(self, products):
        """Aktualisiert Preise in allen Mietverträgen (HTML)."""
        prod_map = {p['name']: p for p in products}

        # Settings laden für Lieferpreis
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}

        def replace_price(match):
            name = match.group(1)
            typ = match.group(2)
            if name == 'lieferung' and typ == 'pauschal':
                return f'<!-- PREIS:lieferung:pauschal -->{settings.get("lieferungPauschal", 100)} €'
            if name == 'kaution' and typ == 'huepfburg':
                return f'<!-- PREIS:kaution:huepfburg -->150 €'
            p = prod_map.get(name)
            if not p:
                return match.group(0)
            if typ == 'maschine':
                return f'<!-- PREIS:{name}:maschine -->{p.get("priceWeekday", 0)} €'
            elif typ == 'komplett':
                total = p.get('priceWeekday', 0) + p.get('priceZutaten', 0)
                return f'<!-- PREIS:{name}:komplett -->{total} €'
            return match.group(0)

        pattern = r'<!-- PREIS:(.+?):(\w+) -->\d+ €'

        for filepath in [MIETVERTRAG_FILE, MIETVERTRAG_HB_FILE]:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    html = f.read()
                html = re.sub(pattern, replace_price, html)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(html)
            except Exception as e:
                print(f'Mietvertrag-Update Fehler ({filepath}): {e}')

    def _save_products(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            products = json.loads(body)
            with open(PRODUCTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(products, f, ensure_ascii=False, indent=2)
            # prices.json auch synchronisieren
            prices = {}
            for p in products:
                prices[p['name']] = {
                    'priceWeekday': p.get('priceWeekday', 0),
                    'priceWeekend': p.get('priceWeekend', 0),
                    'saveWeekend': p.get('saveWeekend', 0)
                }
            with open(PRICES_FILE, 'w', encoding='utf-8') as f:
                json.dump(prices, f, ensure_ascii=False, indent=2)
            # Mietvertrag Partyzubehör aktualisieren
            self._update_mietvertrag(products)
            print(f'Produkte + Preise + Mietvertrag aktualisiert')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            print(f'Produkt-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Bild-Upload ──

    def _upload_image(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            # Base64-Bild dekodieren
            img_data = data.get('image', '')
            filename = data.get('filename', 'upload.jpg')
            # Data-URL Header entfernen falls vorhanden
            if ',' in img_data:
                img_data = img_data.split(',', 1)[1]
            img_bytes = base64.b64decode(img_data)
            # Eindeutigen Dateinamen generieren
            ext = os.path.splitext(filename)[1] or '.jpg'
            safe_name = uuid.uuid4().hex[:8] + ext
            filepath = os.path.join(IMG_DIR, safe_name)
            with open(filepath, 'wb') as f:
                f.write(img_bytes)
            img_path = 'img/' + safe_name
            print(f'Bild hochgeladen: {img_path}')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'path': img_path}).encode())
        except Exception as e:
            print(f'Upload-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def address_string(self):
        # Kein DNS-Reverse-Lookup (beschleunigt Logging massiv)
        return self.client_address[0]

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()


if __name__ == '__main__':
    import socket

    os.chdir(BASE_DIR)
    print(f'E-Mail-Empfaenger: {EMAIL_TO}')
    print(f'SMTP: {SMTP_USER} via {SMTP_HOST}:{SMTP_PORT}')

    class FastServer(ThreadingMixIn, HTTPServer):
        address_family = socket.AF_INET
        allow_reuse_address = True

    server = FastServer(('0.0.0.0', 8081), ProxyHandler)
    print(f'Server laeuft auf http://localhost:8081')
    server.serve_forever()
