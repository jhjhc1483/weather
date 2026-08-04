from http.server import BaseHTTPRequestHandler
from datetime import datetime
import json
import os
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# 서버리스 인스턴스 쿨다운 타임스탬프 (15초 디바운스 디도스 방어)
LAST_TRIGGER_TIME = 0


class handler(BaseHTTPRequestHandler):
    """GitHub Actions workflow_dispatch 트리거 (보안 강화 서버리스 함수)"""

    def do_POST(self):
        global LAST_TRIGGER_TIME

        now = time.time()

        # 1. 쿨다운 체킹 (weather.json의 updated_at 기준 30분=1800초 이내 재요청 방지)
        json_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'weather.json'
        )

        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    wdata = json.load(f)
                updated_at_str = wdata.get('updated_at', '')
                if updated_at_str:
                    dt = datetime.fromisoformat(updated_at_str)
                    updated_ts = dt.timestamp()
                    elapsed = now - updated_ts
                    if elapsed < 1800:
                        remaining_min = max(1, int((1800 - elapsed) // 60) + 1)
                        self._respond(
                            429,
                            {'error': f'최신 갱신 후 30분이 지나지 않았습니다. 약 {remaining_min}분 후 다시 시도해 주세요.'}
                        )
                        return
            except Exception:
                pass

        # 2. 디바운스 (3분/180초 내 중복 트리거 방지)
        if now - LAST_TRIGGER_TIME < 180:
            remaining_sec = max(1, int(180 - (now - LAST_TRIGGER_TIME)))
            self._respond(
                429,
                {'error': f'이미 갱신 작업이 진행 중입니다. (약 2~3분 소요, {remaining_sec}초 후 다시 시도해 주세요.)'}
            )
            return
        LAST_TRIGGER_TIME = now

        # 2. 토큰 검증
        token = (
            os.environ.get('GH_TOKEN', '')
            or os.environ.get('GITHUB_TOKEN', '')
            or os.environ.get('VERCEL_GITHUB_TOKEN', '')
        )
        if not token:
            self._respond(
                500, {'error': 'GH_TOKEN 또는 GITHUB_TOKEN이 설정되지 않았습니다'}
            )
            return

        repo = os.environ.get('GH_REPO', 'jhjhc1483/weather')
        url = f'https://api.github.com/repos/{repo}/actions/workflows/weather-update.yml/dispatches'
        body = json.dumps({'ref': 'main'}).encode('utf-8')

        req = Request(url, data=body, method='POST')
        req.add_header('Authorization', f'token {token}')
        req.add_header('Accept', 'application/vnd.github.v3+json')
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'weather-dashboard')

        try:
            urlopen(req, timeout=20)
            self._respond(200, {'message': '갱신 요청 완료. 약 2~3분 후 데이터가 업데이트됩니다.'})
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
        origin = self.headers.get('Origin', '*')
        # 허용된 출처인 경우 해당 Origin 반환, 그 외 제한
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
