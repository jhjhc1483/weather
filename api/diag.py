from http.server import BaseHTTPRequestHandler
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

KST = timezone(timedelta(hours=9))

BASE_WEATHER = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
BASE_ALERT = "https://apis.data.go.kr/1360000/WthrWrnInfoService"
BASE_AIR = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"


def test_endpoint(url, params_dict, api_key, timeout=10.0):
    encoded_key = quote(api_key, safe='')
    param_str = "&".join([f"{k}={quote(str(v), safe='')}" for k, v in params_dict.items()])
    full_url = f"{url}?serviceKey={encoded_key}&{param_str}"
    
    req = Request(full_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    
    started = time.monotonic()
    try:
        with urlopen(req, timeout=timeout) as response:
            elapsed = round(time.monotonic() - started, 2)
            content = response.read().decode('utf-8', errors='replace')
            
            try:
                data = json.loads(content)
                header = (data.get('response', {}) or {}).get('header', {}) or {}
                rc = str(header.get('resultCode', '00'))
                rmsg = str(header.get('resultMsg', 'NORMAL_SERVICE'))
                
                # 에어코리아 호환
                if not header and 'response' in data:
                    rc = '00'
                    rmsg = 'NORMAL_SERVICE'

                if rc in ('00', '03', '0'):
                    return True, elapsed, f"rc={rc} ({rmsg})"
                return False, elapsed, f"API 오류 rc={rc} ({rmsg})"
            except json.JSONDecodeError:
                snippet = ' '.join(content[:80].split())
                return False, elapsed, f"JSON 파싱 실패 ({snippet})"
    except HTTPError as e:
        elapsed = round(time.monotonic() - started, 2)
        return False, elapsed, f"HTTP {e.code} 오류"
    except URLError as e:
        elapsed = round(time.monotonic() - started, 2)
        return False, elapsed, f"네트워크 오류 ({e.reason})"
    except Exception as e:
        elapsed = round(time.monotonic() - started, 2)
        return False, elapsed, f"오류 발생 ({type(e).__name__})"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        api_key = os.environ.get('DATA_GO_KR_KEY', '')
        if not api_key:
            self._respond(500, {'error': 'DATA_GO_KR_KEY 환경변수가 설정되어 있지 않습니다.'})
            return

        now = datetime.now(KST)
        today_str = now.strftime('%Y%m%d')
        check_time = (now - timedelta(minutes=45))
        bd = check_time.strftime('%Y%m%d')
        bt = check_time.strftime('%H') + '00'

        tests = [
            {'name': '기상청 기상특보 API', 'url': f"{BASE_ALERT}/getWthrWrnMsg", 'params': {'pageNo': '1', 'numOfRows': '5', 'dataType': 'JSON', 'stnId': '108', 'fromTmFc': today_str, 'toTmFc': today_str}},
            {'name': '기상청 초단기실황 API', 'url': f"{BASE_WEATHER}/getUltraSrtNcst", 'params': {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': bd, 'base_time': bt, 'nx': '65', 'ny': '123'}},
            {'name': '기상청 초단기예보 API', 'url': f"{BASE_WEATHER}/getUltraSrtFcst", 'params': {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': bd, 'base_time': bt, 'nx': '65', 'ny': '123'}},
            {'name': '기상청 단기예보 API', 'url': f"{BASE_WEATHER}/getVilageFcst", 'params': {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': today_str, 'base_time': '0200', 'nx': '65', 'ny': '123'}},
            {'name': '에어코리아 미세먼지 API', 'url': f"{BASE_AIR}/getMsrstnAcctoRltmMesureDnsty", 'params': {'returnType': 'json', 'stationName': '양평읍', 'dataTerm': 'DAILY', 'ver': '1.3', 'numOfRows': '1'}},
        ]

        results = []
        all_ok = True

        for t in tests:
            ok, elapsed, msg = test_endpoint(t['url'], t['params'], api_key, timeout=12.0)
            if not ok:
                all_ok = False
            results.append({
                'name': t['name'],
                'ok': ok,
                'elapsed': elapsed,
                'message': msg
            })

        self._respond(200, {
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S KST'),
            'all_ok': all_ok,
            'results': results
        })

    def _respond(self, status, payload):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
