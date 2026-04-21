"""
Booking-Backend + E-Mail-Versand bei Kontaktanfragen.
Buchungen werden lokal in bookings.json gespeichert (keine SuperSaaS-Abhaengigkeit).
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
import shutil
import base64
import uuid
import secrets
import hashlib
import threading
import time

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

# SuperSaaS-Konstanten wurden entfernt. Buchungen laufen jetzt komplett ueber bookings.json.

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
SLIDESHOW_FILE = os.path.join(BASE_DIR, 'slideshow.json')
BLOCKED_DATES_FILE = os.path.join(BASE_DIR, 'blocked_dates.json')
BOOKINGS_FILE = os.path.join(BASE_DIR, 'bookings.json')

# Lock fuer alle Schreib-Zugriffe auf bookings.json
BOOKINGS_LOCK = threading.Lock()

def load_bookings():
    """Liest bookings.json ein. Gibt leere Liste zurueck wenn nicht vorhanden."""
    if not os.path.exists(BOOKINGS_FILE):
        return []
    try:
        with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_bookings(items):
    """Schreibt bookings.json + Backup (.bak). Thread-sicher."""
    with BOOKINGS_LOCK:
        if os.path.exists(BOOKINGS_FILE):
            try:
                shutil.copy2(BOOKINGS_FILE, BOOKINGS_FILE + '.bak')
            except Exception:
                pass
        tmp = BOOKINGS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BOOKINGS_FILE)

def next_booking_id(items):
    """Neue eindeutige Booking-ID: millisekunden-timestamp, nicht kollidierend."""
    bid = int(time.time() * 1000)
    existing = {b.get('id') for b in items}
    while bid in existing:
        bid += 1
    return bid

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
SMTP_USER = os.environ.get('SMTP_USER', 'hupfgaudi@gmail.com')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
EMAIL_TO = os.environ.get('EMAIL_TO', 'buchung@hupfgaudi-vilshofen.de,gutse@gmx.de')

# ── Auth & Security ──
# Admin-Passwort als SHA-256 Hash (Klartext nie im Code!)
# Standard-Passwort: hupfadmin  (BITTE nach erstem Login ändern in .env)
ADMIN_PW_HASH = os.environ.get('ADMIN_PW_HASH',
    'c33bdc57d14885f3499810c72943c32584c109d59dea654956b26ee8458f8b46')  # sha256('hupfadmin')

# Session-Speicher (im RAM, beim Neustart weg)
SESSIONS = {}  # token -> {expires: timestamp}
SESSION_DURATION = 3600 * 8  # 8 Stunden

# Rate-Limiting (pro IP)
RATE_LIMITS = {}  # ip -> [(endpoint, timestamp), ...]

# Erlaubte CORS-Origins (Produktions-Domain hier eintragen)
ALLOWED_ORIGINS = [
    'http://localhost:8081',
    'http://localhost:5500',
    'http://127.0.0.1:8081',
    'http://127.0.0.1:5500',
    'https://www.hupfgaudi-vilshofen.de',
    'https://hupfgaudi-vilshofen.de',
    'null',  # für file:// während Entwicklung
]

# Admin-Endpunkte die Auth brauchen
PROTECTED_ENDPOINTS = [
    '/api/anfragen', '/api/contracts', '/api/confirmed-bookings',
    '/api/freigeben', '/api/anfrage-status', '/api/delete-contract',
    '/api/confirm-booking', '/api/upload-image',
]
# POST-only Auth (GET ist für Homepage ok)
PROTECTED_POST = [
    '/api/products', '/api/prices', '/api/settings', '/api/slideshow',
    '/api/blocked-dates',
]

def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def escape_html(text):
    """HTML-Escaping um XSS zu verhindern."""
    if text is None:
        return ''
    return (str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#39;'))

def validate_email(email):
    """Einfache E-Mail-Validierung."""
    if not email or len(email) > 255:
        return False
    return re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email) is not None

def validate_phone(phone):
    """Telefonnummer: nur Ziffern, +, -, Leerzeichen, (, )."""
    if not phone:
        return True  # optional
    return re.match(r'^[\d\s\+\-\(\)\/]+$', phone) is not None and len(phone) < 40

def sanitize_input(text, max_length=500):
    """Input bereinigen und kürzen."""
    if text is None:
        return ''
    text = str(text).strip()
    return text[:max_length]

def check_rate_limit(ip, endpoint, max_requests=10, window=60):
    """Rate-Limiting: max N Requests pro window Sekunden."""
    now = time.time()
    if ip not in RATE_LIMITS:
        RATE_LIMITS[ip] = []
    # Alte entfernen
    RATE_LIMITS[ip] = [(e, t) for (e, t) in RATE_LIMITS[ip] if now - t < window]
    # Für diesen Endpunkt zählen
    count = sum(1 for (e, t) in RATE_LIMITS[ip] if e == endpoint)
    if count >= max_requests:
        return False
    RATE_LIMITS[ip].append((endpoint, now))
    return True

def clean_sessions():
    """Abgelaufene Sessions entfernen."""
    now = time.time()
    expired = [t for t, s in SESSIONS.items() if s['expires'] < now]
    for t in expired:
        del SESSIONS[t]


class ProxyHandler(SimpleHTTPRequestHandler):

    def _get_session_token(self):
        """Liest Session-Token aus Header oder Cookie."""
        token = self.headers.get('X-Session-Token', '')
        if not token:
            cookie = self.headers.get('Cookie', '')
            for part in cookie.split(';'):
                part = part.strip()
                if part.startswith('session='):
                    token = part[8:]
                    break
        return token

    def _is_authenticated(self):
        """Prüft ob gültiges Session-Token vorhanden."""
        clean_sessions()
        token = self._get_session_token()
        if not token or token not in SESSIONS:
            return False
        return SESSIONS[token]['expires'] > time.time()

    def _require_auth(self):
        """Gibt 401 zurück wenn nicht authentifiziert."""
        if not self._is_authenticated():
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'unauthorized'}).encode())
            return False
        return True

    def _check_rate_limit(self, endpoint, max_req=10, window=60):
        """Gibt 429 zurück wenn Rate-Limit überschritten."""
        ip = self.client_address[0]
        if not check_rate_limit(ip, endpoint, max_req, window):
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'rate_limit_exceeded'}).encode())
            return False
        return True

    def _login(self):
        """Login-Endpunkt: Passwort-Prüfung → Session-Token."""
        # Rate-Limit gegen Brute-Force
        ip = self.client_address[0]
        if not check_rate_limit(ip, '/api/login', max_requests=5, window=300):
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'too_many_attempts'}).encode())
            return
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            password = data.get('password', '')
            if sha256(password) == ADMIN_PW_HASH:
                token = secrets.token_urlsafe(32)
                SESSIONS[token] = {'expires': time.time() + SESSION_DURATION}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'token': token}).encode())
            else:
                self.send_response(403)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'wrong_password'}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _logout(self):
        """Session-Token invalidieren."""
        token = self._get_session_token()
        if token in SESSIONS:
            del SESSIONS[token]
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'success': True}).encode())

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
            if not self._require_auth(): return
            self._get_anfragen()
        elif self.path.startswith('/api/settings'):
            self._get_settings()
        elif self.path.startswith('/api/confirmed-bookings'):
            if not self._require_auth(): return
            self._get_confirmed_bookings()
        elif self.path.startswith('/api/contracts'):
            if not self._require_auth(): return
            self._get_contracts()
        elif self.path.startswith('/api/slideshow'):
            self._get_slideshow()
        elif self.path.startswith('/api/weekend-deal'):
            self._get_weekend_deal()
        elif self.path.startswith('/api/blocked-dates'):
            self._get_blocked_dates()
        elif self.path.startswith('/api/check-session'):
            self.send_response(200 if self._is_authenticated() else 401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'authenticated': self._is_authenticated()}).encode())
        else:
            super().do_GET()

    def do_POST(self):
        # Login/Logout - öffentlich
        if self.path.startswith('/api/login'):
            self._login()
            return
        if self.path.startswith('/api/logout'):
            self._logout()
            return
        # Öffentliche Endpunkte (mit Rate-Limit)
        if self.path.startswith('/api/send-email'):
            if not self._check_rate_limit('send-email', max_req=3, window=300): return
            self._send_email()
        elif self.path.startswith('/api/create-booking'):
            if not self._check_rate_limit('create-booking', max_req=3, window=300): return
            self._create_booking()
        elif self.path.startswith('/api/submit-contract'):
            if not self._check_rate_limit('submit-contract', max_req=5, window=300): return
            self._submit_contract()
        # Admin-Endpunkte (Auth erforderlich)
        elif self.path.startswith('/api/prices'):
            if not self._require_auth(): return
            self._save_prices()
        elif self.path.startswith('/api/products'):
            if not self._require_auth(): return
            self._save_products()
        elif self.path.startswith('/api/upload-image'):
            if not self._require_auth(): return
            self._upload_image()
        elif self.path.startswith('/api/settings'):
            if not self._require_auth(): return
            self._save_settings()
        elif self.path.startswith('/api/confirm-booking'):
            if not self._require_auth(): return
            self._confirm_booking()
        elif self.path.startswith('/api/delete-contract'):
            if not self._require_auth(): return
            self._delete_contract()
        elif self.path.startswith('/api/freigeben'):
            if not self._require_auth(): return
            self._freigeben()
        elif self.path.startswith('/api/anfrage-status'):
            if not self._require_auth(): return
            self._anfrage_status()
        elif self.path.startswith('/api/slideshow'):
            if not self._require_auth(): return
            self._save_slideshow()
        elif self.path.startswith('/api/blocked-dates'):
            if not self._require_auth(): return
            self._save_blocked_dates()
        elif self.path == '/api/bookings' or self.path.startswith('/api/bookings?'):
            if not self._require_auth(): return
            self._create_booking_entry()
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        if self.path.startswith('/api/bookings/'):
            if not self._require_auth(): return
            booking_id = self.path.split('/api/bookings/')[-1].split('?')[0]
            self._update_booking_entry(booking_id)
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith('/api/bookings/'):
            if not self._require_auth(): return
            booking_id = self.path.split('/api/bookings/')[-1].split('?')[0]
            self._delete_booking_entry(booking_id)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Session-Token')
        self.end_headers()

    # ── Buchungen (lokale DB) ──

    def _proxy_bookings(self):
        """Liest Buchungen aus bookings.json, filtert nach from/to-Zeitraum."""
        params = parse_qs(urlparse(self.path).query)
        from_date = params.get('from', [''])[0]
        to_date = params.get('to', [''])[0]

        items = load_bookings()

        # Zeitraum-Filter: start <= to_date UND finish >= from_date (Ueberlappung)
        def in_range(b):
            if b.get('deleted'):
                return False
            start = (b.get('start') or '')[:10]
            finish = (b.get('finish') or '')[:10]
            if from_date and finish and finish < from_date:
                return False
            if to_date and start and start > to_date:
                return False
            return True

        filtered = [b for b in items if in_range(b)]
        body = json.dumps(filtered, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_free(self):
        """Berechnet freie Ressourcen ab from_date aus bookings.json + products.json."""
        params = parse_qs(urlparse(self.path).query)
        from_date = params.get('from', [''])[0]

        # Alle Produkt-Namen holen (Ressourcen)
        try:
            with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                products = json.load(f)
            all_res = [p.get('apiName') or p.get('name') for p in products if p.get('name')]
        except Exception:
            all_res = []

        # Gebuchte Ressourcen am angefragten Tag
        bookings = load_bookings()
        booked_at_date = set()
        for b in bookings:
            if b.get('deleted'):
                continue
            start = (b.get('start') or '')[:10]
            finish = (b.get('finish') or '')[:10]
            if start and finish and start <= from_date <= finish:
                booked_at_date.add(b.get('res_name'))

        free = [{'res_name': r} for r in all_res if r not in booked_at_date]
        body = json.dumps(free, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return None

    def _create_booking_entry(self):
        """POST /api/bookings - neue Buchung anlegen (Dashboard)."""
        data = self._read_json_body()
        if not isinstance(data, dict):
            return self._json_response(400, {'error': 'invalid JSON body'})

        items = load_bookings()
        new_id = data.get('id') or next_booking_id(items)
        now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        entry = {
            'id': new_id,
            'start': data.get('start', ''),
            'finish': data.get('finish', data.get('start', '')),
            'res_name': data.get('res_name', ''),
            'full_name': data.get('full_name', ''),
            'email': data.get('email', ''),
            'mobile': data.get('mobile', ''),
            'address': data.get('address', ''),
            'field_1_r': data.get('field_1_r', ''),
            'field_2_r': data.get('field_2_r', ''),
            'price': data.get('price', 0),
            'status': data.get('status', 100),
            'status_message': data.get('status_message', 'Freigegeben'),
            'created_on': data.get('created_on', now_iso),
            'updated_on': now_iso,
            'deleted': False,
        }
        # Zusaetzliche Felder aus data uebernehmen (z.B. created_by)
        for k, v in data.items():
            if k not in entry:
                entry[k] = v

        items.append(entry)
        save_bookings(items)
        return self._json_response(201, entry)

    def _update_booking_entry(self, booking_id):
        """PUT /api/bookings/<id> - Buchung updaten."""
        data = self._read_json_body()
        if not isinstance(data, dict):
            return self._json_response(400, {'error': 'invalid JSON body'})

        try:
            target_id = int(booking_id)
        except ValueError:
            return self._json_response(400, {'error': 'invalid id'})

        items = load_bookings()
        for b in items:
            if b.get('id') == target_id:
                for k, v in data.items():
                    if k == 'id':
                        continue
                    b[k] = v
                b['updated_on'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                save_bookings(items)
                return self._json_response(200, b)
        return self._json_response(404, {'error': 'booking not found'})

    def _delete_booking_entry(self, booking_id):
        """DELETE /api/bookings/<id> - Soft-Delete."""
        try:
            target_id = int(booking_id)
        except ValueError:
            return self._json_response(400, {'error': 'invalid id'})

        items = load_bookings()
        for b in items:
            if b.get('id') == target_id:
                b['deleted'] = True
                b['updated_on'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                save_bookings(items)
                return self._json_response(200, {'ok': True, 'id': target_id})
        return self._json_response(404, {'error': 'booking not found'})

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

    # ── Buchungsbestätigungs-E-Mail (wiederverwendbar) ──

    def _send_booking_confirmation(self, email, name, product, date_str, lieferung='', extras=None):
        """Sendet eine schöne Buchungsbestätigung an den Kunden."""
        from datetime import datetime
        def fmt(d):
            if not d: return '—'
            try: return datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m.%Y')
            except: return d

        date_display = fmt(date_str)
        base_url = 'https://www.hupfgaudi-vilshofen.de'

        extras_html = ''
        if extras:
            extras_html = '<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:12px 16px;color:#888;font-size:13px;width:40px;vertical-align:top">🎉</td><td style="padding:12px 16px;color:#333;font-size:14px"><strong>Extras:</strong><br>' + '<br>'.join(extras) + '</td></tr>'

        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:560px;margin:20px auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)">
  <!-- Header -->
  <div style="background:linear-gradient(135deg,#ff5a1f 0%,#ff8c42 50%,#ffcc00 100%);padding:32px 24px;text-align:center">
    <p style="margin:0;font-size:28px">🎉</p>
    <h1 style="margin:8px 0 0;color:#fff;font-size:22px;font-weight:800;letter-spacing:-0.5px">Buchung bestätigt!</h1>
    <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:14px">HupfGaudi Vilshofen</p>
  </div>

  <!-- Body -->
  <div style="padding:28px 24px">
    <p style="margin:0 0 20px;font-size:15px;color:#333">Hallo <strong>{name}</strong>,<br>wir freuen uns – deine Buchung ist bestätigt! Hier noch einmal alle Details:</p>

    <!-- Details Box -->
    <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:12px;overflow:hidden;border:1px solid #eee" cellpadding="0" cellspacing="0">
      <tr style="border-bottom:1px solid #f0f0f0">
        <td style="padding:12px 16px;color:#888;font-size:13px;width:40px;vertical-align:top">🏰</td>
        <td style="padding:12px 16px;color:#333;font-size:14px"><strong>{product}</strong></td>
      </tr>
      <tr style="border-bottom:1px solid #f0f0f0">
        <td style="padding:12px 16px;color:#888;font-size:13px">📅</td>
        <td style="padding:12px 16px;color:#333;font-size:14px;font-weight:700">{date_display}</td>
      </tr>
      {'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:12px 16px;color:#888;font-size:13px">🚗</td><td style="padding:12px 16px;color:#333;font-size:14px">' + lieferung + '</td></tr>' if lieferung else ''}
      {extras_html}
    </table>

    <!-- Abholadresse -->
    <div style="margin:20px 0;padding:16px 18px;background:linear-gradient(135deg,#fff8e7,#fff5f0);border-radius:12px;border:1px solid #ffe0b2">
      <p style="margin:0 0 4px;font-weight:800;font-size:13px;color:#e65100">📍 ABHOLADRESSE</p>
      <p style="margin:0;font-size:14px;color:#333">Böcklbacher Str. 7, 94474 Vilshofen (Alkofen)</p>
    </div>

    <!-- Checkliste -->
    <div style="margin:20px 0;padding:16px 18px;background:#f0fdf4;border-radius:12px;border:1px solid #bbf7d0">
      <p style="margin:0 0 8px;font-weight:800;font-size:13px;color:#166534">✅ BITTE MITBRINGEN</p>
      <p style="margin:0 0 4px;font-size:14px;color:#333">• Personalausweis</p>
      <p style="margin:0 0 4px;font-size:14px;color:#333">• Kaution: <strong>150 €</strong> (Hüpfburg) bzw. <strong>100 €</strong> (Partyzubehör) in bar</p>
      <p style="margin:0;font-size:14px;color:#333">• Großes Fahrzeug / Anhänger (bei Selbstabholung)</p>
    </div>

    <!-- Mietvertrag Button -->
    <div style="text-align:center;margin:24px 0">
      <a href="{base_url}/mietvertrag-huepfburg.html" style="display:inline-block;padding:14px 36px;background:#ff5a1f;color:#fff;text-decoration:none;border-radius:50px;font-weight:800;font-size:15px;box-shadow:0 4px 12px rgba(255,90,31,.3)">📄 Mietvertrag ansehen</a>
    </div>

    <!-- Kontakt -->
    <div style="margin:20px 0;padding:16px 18px;background:#f8f9fa;border-radius:12px;text-align:center">
      <p style="margin:0 0 8px;font-weight:700;font-size:13px;color:#666">FRAGEN? WIR SIND FÜR DICH DA!</p>
      <p style="margin:0">
        <a href="tel:+4915128861367" style="color:#ff5a1f;text-decoration:none;font-weight:700;font-size:14px">📱 0151 / 28861367</a>
        &nbsp;&nbsp;·&nbsp;&nbsp;
        <a href="https://wa.me/4915128861367" style="color:#25d366;text-decoration:none;font-weight:700;font-size:14px">💬 WhatsApp</a>
      </p>
    </div>

    <p style="margin:20px 0 0;font-size:14px;color:#555;text-align:center">Wir freuen uns auf dich! 🎈</p>
  </div>

  <!-- Footer -->
  <div style="background:#1a1a2e;padding:18px 24px;text-align:center">
    <p style="margin:0;color:#fff;font-weight:800;font-size:14px">Hupf<span style="color:#ffcc00">Gaudi</span> Vilshofen</p>
    <p style="margin:4px 0 0;color:rgba(255,255,255,.5);font-size:11px">Böcklbacher Str. 7 · 94474 Vilshofen · hupfgaudi@gmail.com</p>
  </div>
</div>
</body></html>"""

        subject = f'Buchungsbestätigung: {product} am {date_display} – HupfGaudi Vilshofen'
        text = f'Hallo {name},\n\ndeine Buchung ist bestätigt!\n\nProdukt: {product}\nDatum: {date_display}\n\nAbholadresse: Böcklbacher Str. 7, 94474 Vilshofen\nBitte Personalausweis + Kaution mitbringen.\n\nDein HupfGaudi Team'

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f'HupfGaudi Vilshofen <{SMTP_USER}>'
        msg['To'] = email
        msg['Reply-To'] = SMTP_USER
        msg.attach(MIMEText(text, 'plain', 'utf-8'))
        msg.attach(MIMEText(html, 'html', 'utf-8'))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [email], msg.as_string())
        print(f'Buchungsbestätigung gesendet an: {email}')

    # ── E-Mail-Versand ──

    def _send_email(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            # Input sanitieren + validieren
            name       = sanitize_input(data.get('name', ''), 100)
            email_raw  = sanitize_input(data.get('email', ''), 255)
            telefon    = sanitize_input(data.get('telefon', ''), 40)
            strasse    = sanitize_input(data.get('strasse', ''), 150)
            ort        = sanitize_input(data.get('ort', ''), 100)
            datum      = sanitize_input(data.get('datum', ''), 20)
            burg       = sanitize_input(data.get('burg', ''), 100)
            lieferung  = sanitize_input(data.get('lieferung', ''), 100)
            extras_raw = data.get('extras', [])
            extras     = [sanitize_input(e, 100) for e in extras_raw[:10]] if isinstance(extras_raw, list) else []
            untergrund = sanitize_input(data.get('untergrund', ''), 100)
            nachricht  = sanitize_input(data.get('nachricht', ''), 2000)
            agb_akzeptiert = bool(data.get('agb_akzeptiert', False))
            ausweis_einwilligung = bool(data.get('ausweis_einwilligung', False))

            # E-Mail validieren
            if not validate_email(email_raw):
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'invalid_email'}).encode())
                return
            email = email_raw

            if telefon and not validate_phone(telefon):
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'invalid_phone'}).encode())
                return

            # Für HTML-E-Mail escapen (XSS-Schutz)
            h_name = escape_html(name)
            h_email = escape_html(email)
            h_telefon = escape_html(telefon)
            h_strasse = escape_html(strasse)
            h_ort = escape_html(ort)
            h_datum = escape_html(datum)
            h_burg = escape_html(burg)
            h_lieferung = escape_html(lieferung)
            h_untergrund = escape_html(untergrund)
            h_nachricht = escape_html(nachricht).replace('\n', '<br>')
            h_extras = [escape_html(e) for e in extras]

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
                            <td style="padding: 10px 0; color: #333;">{h_name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 10px 0; font-weight: bold; color: #555;">✉️ E-Mail:</td>
                            <td style="padding: 10px 0;"><a href="mailto:{h_email}" style="color: #ff5a1f;">{h_email}</a></td>
                        </tr>
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">📱 Telefon:</td><td style="padding: 10px 0;"><a href="tel:' + h_telefon + '" style="color: #ff5a1f;">' + h_telefon + '</a></td></tr>' if telefon else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🏠 Adresse:</td><td style="padding: 10px 0; color: #333;">' + h_strasse + ', ' + h_ort + '</td></tr>' if strasse else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">📅 Wunschdatum:</td><td style="padding: 10px 0; color: #333;">' + h_datum + '</td></tr>' if datum else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🏰 Hüpfburg:</td><td style="padding: 10px 0; color: #333; font-weight: bold;">' + h_burg + '</td></tr>' if burg else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🚗 Abholung/Lieferung:</td><td style="padding: 10px 0; color: #333;">' + h_lieferung + '</td></tr>' if lieferung else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">🎉 Partyzubehör:</td><td style="padding: 10px 0; color: #333;">' + '<br>'.join('• ' + e for e in h_extras) + '</td></tr>' if extras else ''}
                        {'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 10px 0; font-weight: bold; color: #555;">📍 Aufstellplatz:</td><td style="padding: 10px 0; color: #333;">' + h_untergrund + '</td></tr>' if untergrund else ''}
                    </table>
                    {'<div style="margin-top: 16px; padding: 14px; background: #fff; border-radius: 8px; border-left: 4px solid #ff5a1f;"><strong style="color: #555;">💬 Nachricht:</strong><p style="margin: 8px 0 0; color: #333;">' + h_nachricht + '</p></div>' if nachricht else ''}
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
                'strasse': strasse,
                'ort': ort,
                'datum': datum,
                'burg': burg,
                'lieferung': lieferung,
                'extras': extras,
                'untergrund': untergrund,
                'nachricht': nachricht,
                'agb_akzeptiert': agb_akzeptiert,
                'ausweis_einwilligung': ausweis_einwilligung
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

            # Automatisch Vertrag in contracts.json speichern (Kunde hat AGB akzeptiert)
            if agb_akzeptiert and burg:
                try:
                    contract = {
                        'id': anfrage['id'],
                        'submitted_at': anfrage['timestamp'],
                        'contract_type': 'huepfburg',
                        'contract_source': 'kontaktformular',
                        'mieter_name': name,
                        'mieter_strasse': strasse,
                        'mieter_ort': ort,
                        'mieter_telefon': telefon,
                        'mieter_email': email,
                        'modell': burg,
                        'datum_start': datum,
                        'lieferung': lieferung,
                        'agb_akzeptiert': True,
                        'ausweis_einwilligung': ausweis_einwilligung
                    }
                    try:
                        with open(CONTRACTS_FILE, 'r', encoding='utf-8') as f:
                            contracts = json.load(f)
                    except Exception:
                        contracts = []
                    contracts.insert(0, contract)
                    with open(CONTRACTS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(contracts, f, ensure_ascii=False, indent=2)
                    print(f'Vertrag automatisch erstellt: {name} - {burg}')
                except Exception as e:
                    print(f'Vertrag-Speicher-Fehler: {e}')

            # Automatische Eingangsbestätigung an den Kunden
            if email:
                try:
                    auto_html = f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background: linear-gradient(135deg, #ff5a1f, #ffcc00); padding: 24px; border-radius: 12px 12px 0 0; text-align: center;">
                            <h1 style="color: white; margin: 0; font-size: 1.4rem;">🎈 Danke für deine Anfrage!</h1>
                            <p style="color: rgba(255,255,255,0.9); margin: 6px 0 0; font-size: 0.95rem;">HupfGaudi Vilshofen</p>
                        </div>
                        <div style="background: #f9f9f9; padding: 24px; border-radius: 0 0 12px 12px; border: 1px solid #eee;">
                            <p style="font-size: 1rem; color: #333;">Hallo {name},</p>
                            <p style="font-size: 0.95rem; color: #555;">vielen Dank für deine Anfrage bei HupfGaudi Vilshofen! Wir haben deine Nachricht erhalten und melden uns so schnell wie möglich bei dir.</p>
                            <div style="background: #fff8e7; border-radius: 10px; padding: 16px; margin: 20px 0; border: 1px solid #ffcc00;">
                                <p style="margin: 0; font-size: 0.9rem; color: #555;"><strong>Deine Anfrage:</strong></p>
                                {'<p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">🏰 ' + burg + '</p>' if burg else ''}
                                {'<p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">📅 ' + datum + '</p>' if datum else ''}
                                {'<p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">🎉 ' + ', '.join(extras) + '</p>' if extras else ''}
                            </div>
                            {'<div style="background:#d1fae5;border-radius:8px;padding:12px;margin:16px 0;border:1px solid #6ee7b7"><p style="margin:0;font-size:0.88rem;color:#065f46">✅ <strong>Mietvertrag akzeptiert</strong> – Du hast den Mietvertrag, die AGB und Datenschutzerklärung akzeptiert. Bitte bringe zur Abholung deinen Personalausweis mit.</p></div>' if agb_akzeptiert else ''}
                            <p style="font-size: 0.9rem; color: #555;">Wir antworten in der Regel innerhalb weniger Stunden. Bei dringenden Fragen erreichst du uns direkt:</p>
                            <div style="background: #f0f0f0; border-radius: 8px; padding: 14px; margin-top: 16px;">
                                <p style="margin: 0; font-size: 0.9rem; color: #555;">📱 <a href="tel:+4915128861367" style="color: #ff5a1f;">0151 / 28861367</a></p>
                                <p style="margin: 4px 0 0; font-size: 0.9rem; color: #555;">💬 <a href="https://wa.me/4915128861367" style="color: #ff5a1f;">WhatsApp</a></p>
                            </div>
                            <p style="margin-top: 16px; font-size: 0.9rem; color: #555;">Wir freuen uns auf dich! 🎈</p>
                            <p style="font-size: 0.9rem; color: #555;">Dein HupfGaudi Team</p>
                        </div>
                    </body>
                    </html>
                    """
                    auto_subject = f'Deine Anfrage bei HupfGaudi Vilshofen – wir melden uns!'
                    auto_msg = MIMEMultipart('alternative')
                    auto_msg['Subject'] = auto_subject
                    auto_msg['From'] = f'HupfGaudi Vilshofen <{SMTP_USER}>'
                    auto_msg['To'] = email
                    auto_msg['Reply-To'] = SMTP_USER
                    auto_text = f'Hallo {name},\n\nvielen Dank für deine Anfrage bei HupfGaudi Vilshofen! Wir melden uns so schnell wie möglich.\n\nBei dringenden Fragen: 0151/28861367 oder WhatsApp.\n\nDein HupfGaudi Team'
                    auto_msg.attach(MIMEText(auto_text, 'plain', 'utf-8'))
                    auto_msg.attach(MIMEText(auto_html, 'html', 'utf-8'))
                    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                        server.login(SMTP_USER, SMTP_PASS)
                        server.sendmail(SMTP_USER, [email], auto_msg.as_string())
                    print(f'Eingangsbestätigung gesendet an: {email}')
                except Exception as e:
                    print(f'Eingangsbestätigung Fehler: {e}')

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

    # ── Buchung aus Kontaktformular anlegen ──

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

    def _create_supersaas_booking(self, resource_id, name, email, telefon, datum, untergrund, info, res_name=None):
        """Legt eine Buchung in bookings.json an (frueher SuperSaaS-Call)."""
        items = load_bookings()
        new_id = next_booking_id(items)
        now_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        # res_name aus resource_id ableiten, falls nicht explizit uebergeben
        if not res_name:
            for n, rid in getattr(self, 'RESOURCE_MAP', {}).items():
                if rid == resource_id:
                    res_name = n
                    break

        entry = {
            'id': new_id,
            'start': f'{datum}T08:00',
            'finish': f'{datum}T19:00',
            'resource_id': resource_id,
            'res_name': res_name or '',
            'full_name': name,
            'email': email,
            'mobile': telefon,
            'address': untergrund or 'Nicht angegeben',
            'field_1_r': untergrund or '',
            'field_2_r': info or '',
            'price': 0,
            'status': 2,
            'status_message': 'Freigabe steht noch aus',
            'created_on': now_iso,
            'updated_on': now_iso,
            'created_by': 'Kontaktformular',
            'deleted': False,
        }
        items.append(entry)
        save_bookings(items)
        return json.dumps(entry).encode('utf-8')

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
                        print(f'Buchung angelegt: {burg} am {datum} fuer {name}')
                    except Exception as e:
                        results.append(f'Huepfburg-Fehler: {e}')
                        print(f'Fehler (Huepfburg): {e}')

            # Extras buchen (Popcorn, Zuckerwatte, Icecream Roll)
            for extra in extras:
                resource_id = self._find_resource_id(extra)
                if resource_id:
                    try:
                        self._create_supersaas_booking(
                            resource_id, name, email, telefon, datum, untergrund, extra
                        )
                        results.append(f'Extra gebucht: {extra}')
                        print(f'Buchung angelegt: {extra} am {datum} fuer {name}')
                    except Exception as e:
                        results.append(f'Extra-Fehler: {e}')
                        print(f'Fehler (Extra): {e}')

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

    # ── Wochenendangebot ──

    def _get_weekend_deal(self):
        try:
            from datetime import datetime, timedelta

            today = datetime.now().date()
            # Nächsten Freitag finden
            days_until_friday = (4 - today.weekday()) % 7
            if days_until_friday == 0 and today.weekday() > 4:
                days_until_friday = 7
            if days_until_friday == 0:
                days_until_friday = 7 if today.weekday() != 4 else 0
            friday = today + timedelta(days=max(days_until_friday, 1) if today.weekday() >= 5 else days_until_friday or 7)
            sunday = friday + timedelta(days=2)

            # Produkte laden
            with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                products = json.load(f)
            hupfburgen = [p for p in products if p.get('type') == 'hupfburg' and p.get('active')]

            # Buchungen fuers Wochenende aus bookings.json holen
            from_date = friday.isoformat()
            to_date = (sunday + timedelta(days=1)).isoformat()
            booked_names = set()
            bookings = []

            try:
                all_items = load_bookings()
                for b in all_items:
                    if b.get('deleted'):
                        continue
                    start = (b.get('start') or '')[:10]
                    finish = (b.get('finish') or '')[:10]
                    if finish < from_date or start > to_date:
                        continue
                    bookings.append(b)
                    booked_names.add(b.get('res_name', ''))
            except Exception as e:
                print(f'Weekend-Deal: bookings.json nicht lesbar ({e})')

            # Prüfe welche Tage (Fr/Sa/So) einzeln frei sind
            fri_booked = set()
            sat_booked = set()
            sun_booked = set()
            try:
                for b in bookings:
                    if b.get('deleted'):
                        continue
                    b_start = datetime.strptime(b['start'][:10], '%Y-%m-%d').date()
                    b_end = datetime.strptime(b['finish'][:10], '%Y-%m-%d').date()
                    name = b.get('res_name', '')
                    if b_start <= friday <= b_end: fri_booked.add(name)
                    if b_start <= friday + timedelta(days=1) <= b_end: sat_booked.add(name)
                    if b_start <= sunday <= b_end: sun_booked.add(name)
            except Exception:
                pass

            # Freie Hüpfburgen finden
            free_hb = []
            for p in hupfburgen:
                api_name = p.get('apiName', p['name'])
                if api_name in booked_names:
                    continue
                img = p.get('images', [p.get('image', '')])[0] if p.get('images') else p.get('image', '')
                # Ganzes Wochenende frei?
                whole_weekend = api_name not in fri_booked and api_name not in sat_booked and api_name not in sun_booked
                day_price = p['priceWeekday']
                day_deal = round(day_price * 0.8)
                entry = {
                    'name': p['name'],
                    'displayName': p.get('displayName', p['name']),
                    'image': img,
                    'originalPrice': day_price,
                    'dealPrice': day_deal,
                    'discount': 20,
                    'dealType': 'hupfburg',
                    'wholeWeekend': whole_weekend
                }
                if whole_weekend:
                    we_price = day_price * 3
                    we_deal = round(we_price * 0.8)
                    entry['weekendOriginal'] = we_price
                    entry['weekendDeal'] = we_deal
                free_hb.append(entry)

            # Freies Partyzubehör finden (20% auf Maschinenpreis)
            equipment = [p for p in products if p.get('type') == 'equipment' and p.get('active')]
            free_eq = []
            for p in equipment:
                api_name = p.get('apiName', p['name'])
                if api_name not in booked_names:
                    discount = round(p['priceWeekday'] * 0.8)
                    free_eq.append({
                        'name': p['name'],
                        'displayName': p.get('displayName', p['name']),
                        'image': p.get('images', [p.get('image', '')])[0] if p.get('images') else p.get('image', ''),
                        'originalPrice': p['priceWeekday'],
                        'dealPrice': discount,
                        'priceZutaten': p.get('priceZutaten', 0),
                        'discount': 20,
                        'dealType': 'equipment'
                    })

            # 1 Hüpfburg + 1 Partyzubehör
            deals = []
            if free_hb: deals.append(free_hb[0])
            if free_eq: deals.append(free_eq[0])

            result = {
                'friday': friday.isoformat(),
                'sunday': sunday.isoformat(),
                'fridayFormatted': friday.strftime('%d.%m.%Y'),
                'sundayFormatted': sunday.strftime('%d.%m.%Y'),
                'deals': deals
            }

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode())
        except Exception as e:
            print(f'Weekend-Deal Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Vertrag löschen ──

    def _delete_contract(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            contract_id = data.get('id')
            try:
                with open(CONTRACTS_FILE, 'r', encoding='utf-8') as f:
                    contracts = json.load(f)
            except Exception:
                contracts = []
            contracts = [c for c in contracts if c.get('id') != contract_id]
            with open(CONTRACTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(contracts, f, ensure_ascii=False, indent=2)
            print(f'Vertrag geloescht: {contract_id}')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Gesperrte Tage ──

    def _get_blocked_dates(self):
        try:
            with open(BLOCKED_DATES_FILE, 'r', encoding='utf-8') as f:
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

    def _save_blocked_dates(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            dates = json.loads(body)
            with open(BLOCKED_DATES_FILE, 'w', encoding='utf-8') as f:
                json.dump(dates, f, ensure_ascii=False, indent=2)
            print(f'Sperrtage aktualisiert: {len(dates)} Eintraege')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Anfrage ablehnen / löschen ──

    def _anfrage_status(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            anfrage_id = data.get('id')
            action = data.get('action', '')

            try:
                with open(ANFRAGEN_FILE, 'r', encoding='utf-8') as f:
                    anfragen = json.load(f)
            except Exception:
                anfragen = []

            if action == 'loeschen':
                anfragen = [a for a in anfragen if a.get('id') != anfrage_id]
                print(f'Anfrage geloescht: {anfrage_id}')
            elif action == 'ablehnen':
                for a in anfragen:
                    if a.get('id') == anfrage_id:
                        a['abgelehnt'] = True
                        from datetime import datetime
                        a['abgelehnt_am'] = datetime.now().isoformat()
                        print(f'Anfrage abgelehnt: {a.get("name", "?")}')
                        break

            with open(ANFRAGEN_FILE, 'w', encoding='utf-8') as f:
                json.dump(anfragen, f, ensure_ascii=False, indent=2)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Anfrage freigeben ──

    def _freigeben(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            anfrage_id = data.get('id')

            # Anfrage in anfragen.json als freigegeben markieren
            try:
                with open(ANFRAGEN_FILE, 'r', encoding='utf-8') as f:
                    anfragen = json.load(f)
            except Exception:
                anfragen = []

            anfrage = None
            for a in anfragen:
                if a.get('id') == anfrage_id:
                    a['freigegeben'] = True
                    from datetime import datetime
                    a['freigegeben_am'] = datetime.now().isoformat()
                    anfrage = a
                    break

            if not anfrage:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Anfrage nicht gefunden'}).encode())
                return

            with open(ANFRAGEN_FILE, 'w', encoding='utf-8') as f:
                json.dump(anfragen, f, ensure_ascii=False, indent=2)

            # Buchung(en) in bookings.json auf "Freigegeben" (status 100) setzen
            # Match: Name + Datum (bei Mehrfach-Buchungen alle zugehoerigen freigeben)
            burg = anfrage.get('burg', '')
            datum = anfrage.get('datum', '')
            if datum:
                try:
                    items = load_bookings()
                    changed = 0
                    for b in items:
                        if b.get('deleted'):
                            continue
                        if not (b.get('start') or '').startswith(datum):
                            continue
                        if (b.get('full_name', '').lower() != anfrage.get('name', '').lower()):
                            continue
                        b['status'] = 100
                        b['status_message'] = 'Freigegeben'
                        b['updated_on'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                        changed += 1
                    if changed:
                        save_bookings(items)
                        print(f'{changed} Buchung(en) freigegeben: {anfrage.get("name")} am {datum}')
                except Exception as e:
                    print(f'Freigabe-Fehler bookings.json: {e}')

            print(f'Anfrage freigegeben: {anfrage.get("name")} - {burg}')

            # Automatische Buchungsbestätigung an den Kunden
            email = anfrage.get('email', '')
            name = anfrage.get('name', '')
            if email and burg:
                try:
                    self._send_booking_confirmation(
                        email, name, burg, datum,
                        lieferung=anfrage.get('lieferung', ''),
                        extras=anfrage.get('extras')
                    )
                except Exception as e:
                    print(f'Bestätigung-E-Mail Fehler: {e}')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        except Exception as e:
            print(f'Freigabe Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    # ── Slideshow ──

    def _get_slideshow(self):
        try:
            with open(SLIDESHOW_FILE, 'r', encoding='utf-8') as f:
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

    def _save_slideshow(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            slides = json.loads(body)
            with open(SLIDESHOW_FILE, 'w', encoding='utf-8') as f:
                json.dump(slides, f, ensure_ascii=False, indent=2)
            print(f'Slideshow aktualisiert: {len(slides)} Bilder')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
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
            start_date = data.get('start', '').split('T')[0] if data.get('start') else ''
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

            # E-Mail senden
            self._send_booking_confirmation(email, name, product_name, start_date, lieferung=delivery_info)

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
            # Größen-Limit: 5 MB für den Request
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 7 * 1024 * 1024:  # 7 MB (Base64 ~33% größer)
                self.send_response(413)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'file_too_large'}).encode())
                return

            body = self.rfile.read(content_length)
            data = json.loads(body)
            img_data = data.get('image', '')
            filename = data.get('filename', 'upload.jpg')
            if ',' in img_data:
                img_data = img_data.split(',', 1)[1]
            img_bytes = base64.b64decode(img_data)

            # Tatsächliche Bildgröße prüfen (5 MB max)
            if len(img_bytes) > 5 * 1024 * 1024:
                self.send_response(413)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'image_too_large'}).encode())
                return

            # Dateityp via Magic Bytes prüfen
            allowed_types = {
                b'\xff\xd8\xff': '.jpg',         # JPEG
                b'\x89PNG\r\n\x1a\n': '.png',   # PNG
                b'GIF87a': '.gif',
                b'GIF89a': '.gif',
                b'RIFF': '.webp',  # WebP (check further)
            }
            detected_ext = None
            for magic, ext in allowed_types.items():
                if img_bytes.startswith(magic):
                    # WebP erkennt "RIFF" + später "WEBP"
                    if magic == b'RIFF' and b'WEBP' not in img_bytes[:20]:
                        continue
                    detected_ext = ext
                    break

            if not detected_ext:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'invalid_image_type'}).encode())
                return

            # Sicherer Dateiname (keine Path-Traversal möglich)
            safe_name = uuid.uuid4().hex[:8] + detected_ext
            filepath = os.path.join(IMG_DIR, safe_name)
            with open(filepath, 'wb') as f:
                f.write(img_bytes)
            img_path = 'img/' + safe_name
            print(f'Bild hochgeladen: {img_path} ({len(img_bytes)} bytes)')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True, 'path': img_path}).encode())
        except Exception as e:
            print(f'Upload-Fehler: {e}')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'upload_failed'}).encode())

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
