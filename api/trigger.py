from http.server import BaseHTTPRequestHandler
import json
import os
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class handler(BaseHTTPRequestHandler):
    """GitHub Actions workflow_dispatch 트리거 (Vercel 서버리스 함수)"""

    def do_POST(self):
        token = os.environ.get('GITHUB_TOKEN', '')
        if not token:
            self._respond(500, {'error': 'GITHUB_TOKEN이 설정되지 않았습니다'})
            return

        url = 'https://api.github.com/repos/jhjhc1483/weather/actions/workflows/weather-update.yml/dispatches'
        body = json.dumps({'ref': 'main'}).encode('utf-8')

        req = Request(url, data=body, method='POST')
        req.add_header('Authorization', f'token {token}')
        req.add_header('Accept', 'application/vnd.github.v3+json')
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'weather-dashboard')

        try:
            urlopen(req, timeout=10)
            self._respond(200, {'message': '갱신 요청 완료. 약 2분 후 데이터가 업데이트됩니다.'})
        except HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            self._respond(e.code, {'error': f'GitHub API 오류 ({e.code})', 'detail': error_body[:200]})
        except URLError as e:
            self._respond(500, {'error': str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
