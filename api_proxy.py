"""
Kleiner CORS-Proxy für SuperSaaS API.
Startet auf Port 8081 und leitet Anfragen an SuperSaaS weiter.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.request
import json
import os

SUPERSAAS_ACCOUNT = 'Hupfgaudi-Vilshofen'
SUPERSAAS_API_KEY = 'VClzqXLcmH6QLYs2Ksr_8A'
SCHEDULE_ID = '795126'

class ProxyHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/bookings'):
            self._proxy_bookings()
        elif self.path.startswith('/api/free'):
            self._proxy_free()
        else:
            # Statische Dateien aus dem hupfgaudi-Ordner
            super().do_GET()

    def _proxy_bookings(self):
        from urllib.parse import urlparse, parse_qs
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
        from urllib.parse import urlparse, parse_qs
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

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = HTTPServer(('localhost', 8081), ProxyHandler)
    print('Proxy-Server läuft auf http://localhost:8081')
    server.serve_forever()
