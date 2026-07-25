from http.server import BaseHTTPRequestHandler
import json
import os


class handler(BaseHTTPRequestHandler):
    """weather.json의 updated_at 타임스탬프를 CDN 캐시 없이 직접 반환.
    폴링 시 CDN 정적 캐시를 우회하여 최신 데이터 감지를 보장한다."""

    def do_GET(self):
        json_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'weather.json'
        )

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self._respond(200, {
                'updated_at': data.get('updated_at', ''),
                'time_display': data.get('time_display', ''),
            })
        except FileNotFoundError:
            self._respond(404, {'error': 'weather.json 파일을 찾을 수 없습니다.'})
        except Exception as e:
            self._respond(500, {'error': str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _cors(self):
        origin = self.headers.get('Origin', '') if self.headers else ''
        allowed = origin if origin else '*'
        self.send_header('Access-Control-Allow-Origin', allowed)
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
