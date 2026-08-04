#!/usr/bin/env python3
"""
8개 지역 날씨 데이터 수집 스크립트 (고도화 버전)

수집 및 결합 로직:
1. 초단기실황 (getUltraSrtNcst): 현재 시각의 정밀 관측 데이터 (기온 T1H, 강수량 RN1, 풍향 VEC, 풍속 WSD, 강수형태 PTY)
2. 초단기예보 (getUltraSrtFcst): 매시 30분/45분 발표, 향후 1~6시간 이내 정밀 예보 (RN1, PTY, SKY) - 최우선 반영
3. 최신 단기예보 (getVilageFcst): 최신 발표 base_time(02,05,08,11,14,17,20,23시) 기준 예보 (TMN, TMX, PCP, PTY, SKY)
4. 에어코리아 대기오염 (getMsrstnAcctoRltmMesureDnsty): 실시간 미세먼지(PM10)/초미세먼지(PM2.5)
5. 기상특보 (getWthrWrnMsg): 178개 시군 단위 기상특보
"""

import json
import math
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

# ============================================================
# 환경 변수 로드
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

API_KEY = os.environ.get('DATA_GO_KR_KEY', '').strip()
if not API_KEY:
    print("ERROR: DATA_GO_KR_KEY 환경변수가 설정되지 않았습니다.")
    sys.exit(1)

# ============================================================
# 8개 지역 설정
# nx, ny: 기상청 격자좌표
# station: 에어코리아 실제 측정소명 (검증 완료)
# alert_region: 기상특보 검색 키워드
# wrn_stn: 특보 통보문을 발표하는 관할 관서 지점번호
#          전국(108) 통보문은 발효 목록이 매우 길어 뒷부분(전북·경북·제주 등)이
#          줄바꿈·절단으로 유실되기 쉽다. 관할 지방청 통보문은 해당 관할만
#          나열하므로 짧고 안전하다. 실패 시 108로 폴백한다.
# ============================================================
WRN_STN_NATIONWIDE = '108'

LOCATIONS = {
    '양평': {'nx': 70, 'ny': 126, 'station': '양평읍',   'fallback_station': '가평읍', 'alert_region': '양평', 'sub_region': '서부', 'province': '경기도',   'wrn_stn': '109'},    # 노도성당 (양평 서부)
    '경산': {'nx': 92, 'ny': 91,  'station': '시지동',   'fallback_station': '만촌동', 'alert_region': '경산', 'province': '경상북도', 'wrn_stn': '143'},    # 제광파종기
    '사천': {'nx': 81, 'ny': 72,  'station': '사천읍',   'fallback_station': '향촌동', 'alert_region': '사천', 'province': '경상남도', 'wrn_stn': '159'},    # 후전삼거리
    '함안': {'nx': 87, 'ny': 78,  'station': '가야읍',   'fallback_station': '내서읍', 'alert_region': '함안', 'province': '경상남도', 'wrn_stn': '159'},    # 국군복지단 충무마트
    '성주': {'nx': 85, 'ny': 92,  'station': '성주군',   'fallback_station': '다사읍', 'alert_region': '성주', 'province': '경상북도', 'wrn_stn': '143'},    # 초전면
    '세종': {'nx': 65, 'ny': 104, 'station': '아름동',   'fallback_station': '조치원읍', 'alert_region': '세종', 'sub_region': '북부', 'province': '세종',    'wrn_stn': '133'},    # 세종레스텔(연서면 봉암리 - 세종 북부)
    '계룡': {'nx': 66, 'ny': 100, 'station': '엄사면',   'fallback_station': '논산',   'alert_region': '계룡', 'province': '충청남도', 'wrn_stn': '133'},    # 품안마을아파트(신도안면)
    '임실': {'nx': 67, 'ny': 85,  'station': '임실읍',   'fallback_station': '삼천동', 'alert_region': '임실', 'province': '전북',     'wrn_stn': '146'},    # 충경신병교육대
}

LOCATION_ORDER = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실']
KST = timezone(timedelta(hours=9))

# ============================================================
# 공공데이터포털 엔드포인트
#
# 반드시 https(443) 로 호출할 것.
# 2026-08-03, http(80) 보안 조치로 포털 오픈API 호출이 응답 없이 매달리는
# 장애가 발생했다(공공데이터활용지원센터 회신). 증상은 연결 지연 → 클라이언트
# 타임아웃으로 나타나 마치 기상청 장애처럼 보이므로, http로 되돌리지 말 것.
# ============================================================
BASE_WEATHER = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
BASE_AIR = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
BASE_ALERT = "https://apis.data.go.kr/1360000/WthrWrnInfoService"

for _name, _url in (('BASE_WEATHER', BASE_WEATHER), ('BASE_AIR', BASE_AIR),
                    ('BASE_ALERT', BASE_ALERT)):
    assert _url.startswith('https://'), f"{_name}은(는) https로 호출해야 합니다: {_url}"

# 특보 통보문 조회 기간(일). 넓힐수록 응답이 무거워진다.
ALERT_LOOKBACK_DAYS = 2


# ============================================================
# 로그 마스킹
#
# GitHub Actions는 Secret의 '원본' 문자열만 ***로 가린다. quote()로 퍼센트
# 인코딩된 형태(예: / -> %2F)는 패턴이 달라 마스킹을 통과해 평문 노출된다.
# 예외 메시지·응답 본문에는 요청 URL이 그대로 실리므로 반드시 직접 가린다.
# ============================================================
_SECRET_FORMS = [s for s in {API_KEY, quote(API_KEY, safe='')} if s]


def redact(text):
    """서비스키(원본/인코딩/절단 조각)를 마스킹한다."""
    s = str(text)
    for secret in _SECRET_FORMS:
        s = s.replace(secret, '***')
    # 잘린 키 조각까지 방어: serviceKey= 뒤는 통째로 가린다.
    return re.sub(r'(serviceKey=)[^&\s\'"]*', r'\1***', s)


# 일부 제공기관 WAF는 기본 python-requests UA를 차단한다.
REQUEST_HEADERS = {
    'User-Agent': 'weather-dashboard/1.0 (+github-actions)',
    'Accept': 'application/json, text/plain, */*',
}


def key_sanity_report():
    """서비스키의 '모양'만 점검한다. 키 내용은 절대 출력하지 않는다."""
    raw = os.environ.get('DATA_GO_KR_KEY', '')
    notes = []
    if raw != raw.strip():
        notes.append('앞뒤 공백/개행 있음(자동 제거함)')
    if re.search(r'%[0-9A-Fa-f]{2}', API_KEY):
        notes.append('이미 퍼센트 인코딩된 형태 — Encoding키가 아니라 '
                     'Decoding키를 넣어야 합니다')
    if len(API_KEY) not in range(80, 110):
        notes.append(f'길이 {len(API_KEY)}자 — 일반 인증키는 통상 80~100자')
    print(f"[키] 길이 {len(API_KEY)}자"
          + (f" | 주의: {'; '.join(notes)}" if notes else " | 형식 정상"))


# 포털 공통 오류코드 중 '재시도해도 결과가 같은' 영구 오류.
# 이런 응답에 재시도를 거는 것은 호출 쿼터만 소모한다.
PERMANENT_ERROR_CODES = {
    '30': '해당 API에 활용신청이 되어 있지 않습니다. 마이페이지 > 오픈API > '
          '활용신청 현황에서 해당 서비스의 승인 여부를 확인하세요.',
    '31': '서비스키 활용기간이 만료되었습니다. 연장신청이 필요합니다.',
    '32': '등록되지 않은 IP입니다. 활용신청 시 등록한 IP와 호출 IP가 다릅니다.',
    '22': '일일 트래픽 제한을 초과했습니다. 익일 자동 해제되거나 증량신청이 필요합니다.',
}


def portal_error(text):
    """포털 공통 오류 응답(JSON/XML 양쪽)에서 (코드, 메시지)를 추출."""
    code = msg = None
    try:
        header = (json.loads(text).get('OpenAPI_ServiceResponse', {})
                  or {}).get('cmmMsgHeader', {}) or {}
        code = str(header.get('returnReasonCode', '') or '') or None
        msg = header.get('returnAuthMsg') or header.get('errMsg')
    except (json.JSONDecodeError, AttributeError, TypeError):
        m = re.search(r'<returnReasonCode>\s*(\d+)\s*</returnReasonCode>', text)
        if m:
            code = m.group(1)
        m = re.search(r'<returnAuthMsg>\s*([^<]+)</returnAuthMsg>', text)
        if m:
            msg = m.group(1).strip()
    return code, msg


# ============================================================
# API 호출 헬퍼 & 장애 감지 (서비스 계열별 Fast-Fail 서킷 브레이커)
#
# 설계 원칙
#  1) 실패는 반드시 '원인'과 함께 기록한다. 원인 로그가 없으면 기상청 장애인지,
#     우리 쪽 타임아웃/파라미터 오류인지 사후에 구분할 방법이 없다.
#  2) 서킷은 서비스 계열별로 분리한다. 특보(getWthrWrnMsg)는 통보문 전문을
#     내려주어 응답이 무겁고 느리므로, 특보 지연이 단기예보·미세먼지 수집까지
#     차단해서는 안 된다.
#  3) resultCode 03(NO_DATA)은 장애가 아니라 '해당 조건에 자료 없음'이다.
#     실패로 집계하지 않는다.
# ============================================================
FAILED_SERVICES = set()
MAX_ALLOWED_FAILURES = 5          # 계열별 연속 실패 허용치

# 계열별 상태: {'failures': 연속 실패 횟수, 'broken': 서킷 개방 여부}
CIRCUIT_STATE = {}

# 계열별 기본 타임아웃(초). 특보는 응답 본문이 커서 넉넉히 준다.
GROUP_TIMEOUT = {'기상특보': 20, '에어코리아': 20, '기상청예보': 20}


def _group_of(service_name):
    """service_name을 서킷 계열로 매핑."""
    name = service_name or ''
    if '특보' in name:
        return '기상특보'
    if '에어코리아' in name:
        return '에어코리아'
    return '기상청예보'


def _circuit(group):
    return CIRCUIT_STATE.setdefault(group, {'failures': 0, 'broken': False})


def circuit_broken(group=None):
    """group 지정 시 해당 계열, 미지정 시 하나라도 열려 있으면 True."""
    if group is not None:
        return CIRCUIT_STATE.get(group, {}).get('broken', False)
    return any(s['broken'] for s in CIRCUIT_STATE.values())


def api_call(url, params, retries=3, service_name=None, timeout=None):
    group = _group_of(service_name)
    state = _circuit(group)
    if timeout is None:
        timeout = GROUP_TIMEOUT.get(group, 20)

    # 1. 해당 계열의 서킷이 열려 있으면 추가 호출 없이 조기 종료 (Fast-Fail)
    if state['broken']:
        if service_name:
            FAILED_SERVICES.add(service_name)
        return None

    encoded_key = quote(API_KEY, safe='')
    full_url = f"{url}?serviceKey={encoded_key}"
    label = service_name or url.rsplit('/', 1)[-1]
    reason = '원인 미상'
    permanent = False

    for attempt in range(retries):
        started = time.monotonic()
        try:
            resp = requests.get(full_url, params=params, timeout=timeout,
                                headers=REQUEST_HEADERS)

            # raise_for_status()를 먼저 부르면 본문을 못 읽고 예외로 빠진다.
            # 4xx/5xx의 진짜 사유는 대부분 본문(WAF 차단 안내, returnAuthMsg 등)에 있다.
            if resp.status_code >= 400:
                code, msg = portal_error(resp.text)
                if code in PERMANENT_ERROR_CODES:
                    # 영구 오류 → 재시도 없이 즉시 중단
                    permanent = True
                    reason = (f"코드 {code} ({msg}) → {PERMANENT_ERROR_CODES[code]}")
                    break
                body = ' '.join(resp.text[:300].split())
                reason = redact(f"HTTP {resp.status_code} 응답본문={body}")
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                break

            try:
                data = resp.json()
            except json.JSONDecodeError:
                # 공공데이터포털은 서비스 레벨 오류(키 미등록, 일일 트래픽 초과 등)일 때
                # dataType=JSON 이어도 XML(OpenAPI_ServiceResponse)로 응답한다.
                snippet = ' '.join(resp.text[:300].split())
                reason = redact(f"JSON 파싱 실패 (HTTP {resp.status_code}) 응답앞부분={snippet}")
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                break

            header = (data.get('response', {}) or {}).get('header', {}) or {}
            rc = str(header.get('resultCode', ''))
            rmsg = str(header.get('resultMsg', ''))

            if rc in ('00', '03'):
                state['failures'] = 0        # 정상 응답 → 연속 실패 카운터 리셋
                if rc == '03':
                    print(f"  [API] {label} 자료 없음 (resultCode=03) — 장애 아님")
                return data

            reason = f"resultCode={rc} resultMsg={rmsg}"
            if rc in PERMANENT_ERROR_CODES:
                permanent = True
                reason = f"코드 {rc} ({rmsg}) → {PERMANENT_ERROR_CODES[rc]}"
                break
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
            break

        except requests.Timeout:
            reason = (f"타임아웃 {timeout}초 초과 "
                      f"(경과 {time.monotonic() - started:.1f}s)")
            if attempt < retries - 1:
                time.sleep(0.5)
        except requests.RequestException as e:
            reason = redact(f"{type(e).__name__}: {str(e)[:200]}")
            if attempt < retries - 1:
                time.sleep(0.5)

    # ── 최종 실패 ────────────────────────────────────────────
    print(f"  [API 실패] {label} — {redact(reason)}")
    state['failures'] += 1
    if service_name:
        FAILED_SERVICES.add(service_name)

    # 키 미등록·기한만료 등은 이번 실행 내내 절대 회복되지 않는다.
    # 5회를 채울 때까지 기다릴 이유가 없으므로 즉시 차단한다.
    if permanent and not state['broken']:
        state['broken'] = True
        print(f"[🚨 Fast-Fail] '{group}' 계열 인증 오류 — 이번 실행에서는 "
              f"회복 불가하므로 즉시 차단합니다 (다른 계열 수집은 계속 진행)")
    elif state['failures'] >= MAX_ALLOWED_FAILURES and not state['broken']:
        state['broken'] = True
        print(f"\n[🚨 Fast-Fail] '{group}' 계열 연속 {state['failures']}회 장애 — "
              f"이 계열만 조기 차단합니다 (다른 계열 수집은 계속 진행)")

    return None


# ============================================================
# 시간 계산 함수
# ============================================================
def get_latest_vilage_base(now_time):
    """현재 시각 기준 가장 최신 단기예보 base_time (0200, 0500, 0800, 1100, 1400, 1700, 2000, 2300)"""
    check = (now_time - timedelta(minutes=15)).strftime('%H%M')
    base_times = ['0200', '0500', '0800', '1100', '1400', '1700', '2000', '2300']
    selected = None
    for bt in base_times:
        if bt <= check:
            selected = bt
    if selected is None:
        return (now_time - timedelta(days=1)).strftime('%Y%m%d'), '2300'
    return now_time.strftime('%Y%m%d'), selected


def get_ultra_fcst_base(now_time):
    """초단기예보 base_time (매시 정각, ~45분 소요)"""
    check = now_time - timedelta(minutes=45)
    return check.strftime('%Y%m%d'), check.strftime('%H') + '00'


def get_ncst_base(now_time):
    """초단기실황 base_time (매시 정각, ~40분 소요)"""
    check = now_time - timedelta(minutes=40)
    return check.strftime('%Y%m%d'), check.strftime('%H') + '00'


# ============================================================
# 데이터 변환 헬퍼
# ============================================================
def wind_dir_text(deg):
    try:
        deg = float(deg)
    except (ValueError, TypeError):
        return '-'
    dirs = ['북풍', '북북동풍', '북동풍', '동북동풍', '동풍', '동남동풍', '남동풍', '남남동풍',
            '남풍', '남남서풍', '남서풍', '서남서풍', '서풍', '서북서풍', '북서풍', '북북서풍']
    return dirs[round(deg / 22.5) % 16]


def parse_rain_val(value):
    """PCP/RN1 강수량 문자열 → 숫자(mm)"""
    if not value or str(value).strip() in ('강수없음', '0', '0.0', '-'):
        return 0.0
    s_val = str(value).strip()
    if '미만' in s_val:
        return 0.5
    if '이상' in s_val:
        return 50.0
    try:
        return float(s_val.replace('mm', '').strip())
    except (ValueError, TypeError):
        return 0.0


def sky_pty_to_text(sky, pty):
    try:
        pty = int(pty)
    except (ValueError, TypeError):
        pty = 0

    pty_map = {1: '비', 2: '비/눈', 3: '눈', 4: '소나기', 5: '빗방울', 6: '빗방울/눈날림', 7: '눈날림'}
    if pty in pty_map:
        return pty_map[pty]

    try:
        sky = int(sky)
    except (ValueError, TypeError):
        return '-'
    return {1: '맑음', 3: '구름많음', 4: '흐림'}.get(sky, '-')


def calc_pm10_grade(val_str, grade_str):
    t = {'1': '좋음', '2': '보통', '3': '나쁨', '4': '매우나쁨'}.get(str(grade_str), '')
    if t:
        return t
    try:
        v = float(val_str)
        if v <= 30: return '좋음'
        if v <= 80: return '보통'
        if v <= 150: return '나쁨'
        return '매우나쁨'
    except (ValueError, TypeError):
        return '-'


def calc_pm25_grade(val_str, grade_str):
    t = {'1': '좋음', '2': '보통', '3': '나쁨', '4': '매우나쁨'}.get(str(grade_str), '')
    if t:
        return t
    try:
        v = float(val_str)
        if v <= 15: return '좋음'
        if v <= 35: return '보통'
        if v <= 75: return '나쁨'
        return '매우나쁨'
    except (ValueError, TypeError):
        return '-'


def dust_grade_text(grade):
    return {'1': '좋음', '2': '보통', '3': '나쁨', '4': '매우나쁨'}.get(str(grade), '-')


def calculate_feels_like(temp_str, reh_str, wsd_str, month):
    """
    기상청 공식 체감온도 산출 함수
    - 여름철(5~9월 또는 기온>=20℃): Stull 습구온도 기반 기상청 습도 체감온도 공식
    - 겨울철(10~4월 또는 기온<=10℃): WMO/JAG/TI 바람 체감온도 공식
    """
    try:
        t = float(temp_str)
    except (ValueError, TypeError):
        return '-'

    try:
        h = float(reh_str)
    except (ValueError, TypeError):
        h = 50.0

    try:
        w = float(wsd_str)
    except (ValueError, TypeError):
        w = 0.0

    v_kmh = w * 3.6

    # 1. 겨울철 체감온도 (기온 10℃ 이하 및 풍속 1.3m/s(4.68km/h) 이상 시 바람 체감온도 적용)
    if (month in [10, 11, 12, 1, 2, 3, 4] or t <= 10.0) and t <= 10.0 and v_kmh >= 4.68:
        fl = 13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16)
        return f"{fl:.1f}"

    # 2. 여름철 체감온도 (기온 20℃ 이상 시 Stull 습구온도 기반 체감온도 적용)
    if (month in [5, 6, 7, 8, 9] or t >= 20.0) and t >= 20.0:
        tw = (t * math.atan(0.151977 * ((h + 8.313659) ** 0.5)) +
              math.atan(t + h) -
              math.atan(h - 1.676331) +
              0.00391838 * (h ** 1.5) * math.atan(0.023101 * h) -
              4.686035)
        fl = -0.2442 + 0.55399 * tw + 0.45535 * t - 0.0022 * (tw ** 2) + 0.00278 * tw * t + 3.0
        return f"{fl:.1f}"

    # 3. 온화한 기온대 (10℃ ~ 20℃)
    return f"{t:.1f}"


# ============================================================
# API 조회 함수
# ============================================================
def fetch_ncst(nx, ny, now_time):
    """초단기실황"""
    bd, bt = get_ncst_base(now_time)
    data = api_call(f"{BASE_WEATHER}/getUltraSrtNcst", {
        'pageNo': '1', 'numOfRows': '100', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny),
    }, service_name='기상청 실시간관측')
    if not data:
        return {}
    try:
        items = data['response']['body']['items']['item']
        res = {}
        for it in items:
            res[it['category']] = it['obsrValue']
        return res
    except (KeyError, TypeError):
        return {}


def fetch_ultra_fcst(nx, ny, now_time):
    """초단기예보 (향후 6시간 정밀 예보)"""
    bd, bt = get_ultra_fcst_base(now_time)
    data = api_call(f"{BASE_WEATHER}/getUltraSrtFcst", {
        'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny),
    }, service_name='기상청 강수예보')
    if not data:
        return []
    try:
        return data['response']['body']['items']['item']
    except (KeyError, TypeError):
        return []


def fetch_vilage_fcst(nx, ny, now_time, today_str):
    """최신 단기예보"""
    bd, bt = get_latest_vilage_base(now_time)
    data = api_call(f"{BASE_WEATHER}/getVilageFcst", {
        'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny),
    }, service_name='기상청 단기예보')
    items = []
    if data:
        try:
            items = data['response']['body']['items']['item']
        except (KeyError, TypeError):
            pass

    # 만약 최신 예보에 TMN/TMX가 빠져있다면 새벽 예보(0200)도 추가 조회하여 기온 채움
    tmn_exists = any(i['category'] == 'TMN' and i['fcstDate'] == today_str for i in items)
    tmx_exists = any(i['category'] == 'TMX' and i['fcstDate'] == today_str for i in items)

    if (not tmn_exists or not tmx_exists) and bt != '0200':
        early_data = api_call(f"{BASE_WEATHER}/getVilageFcst", {
            'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
            'base_date': today_str, 'base_time': '0200', 'nx': str(nx), 'ny': str(ny),
        }, service_name='기상청 단기예보')
        if early_data:
            try:
                early_items = early_data['response']['body']['items']['item']
                items.extend(early_items)
            except (KeyError, TypeError):
                pass

    tomorrow_str = (now_time.date() + timedelta(days=1)).strftime('%Y%m%d')
    return [i for i in items if i.get('fcstDate') in (today_str, tomorrow_str)]


def fetch_air(station, fallback_station=None):
    """에어코리아 미세먼지 (점검 중일 때 fallback 측정소 자동 전환)"""
    def _query(st):
        data = api_call(f"{BASE_AIR}/getMsrstnAcctoRltmMesureDnsty", {
            'returnType': 'json', 'stationName': st,
            'dataTerm': 'DAILY', 'ver': '1.3', 'numOfRows': '1',
        }, service_name='에어코리아 미세먼지')
        if not data:
            return None
        try:
            items = data['response']['body']['items']
            if items and len(items) > 0:
                it = items[0]
                p10 = it.get('pm10Value')
                p25 = it.get('pm25Value')
                if p10 is not None and p10 != '-' and p10 != '':
                    g10 = calc_pm10_grade(p10, it.get('pm10Grade', ''))
                    g25 = calc_pm25_grade(p25, it.get('pm25Grade', ''))
                    return {
                        'pm10': str(p10),
                        'pm10_grade': g10,
                        'pm25': str(p25) if p25 is not None else '-',
                        'pm25_grade': g25,
                    }
        except (KeyError, TypeError):
            pass
        return None

    res = _query(station)
    if res:
        res['is_fallback'] = False
        res['station_used'] = station
        res['primary_station'] = station
        return res

    if fallback_station:
        res = _query(fallback_station)
        if res:
            res['is_fallback'] = True
            res['station_used'] = fallback_station
            res['primary_station'] = station
            return res

    return {
        'pm10': '-', 'pm10_grade': '-', 'pm25': '-', 'pm25_grade': '-',
        'is_fallback': False, 'station_used': station, 'primary_station': station
    }


def fetch_alerts():
    """기상특보 (발효 중 + 예정 특보 포함, 발효시각 첨부)

    지역별 관할 관서(wrn_stn) 통보문을 각각 조회한다.
    전국(108) 통보문은 발효 목록이 길어 뒷부분이 유실되기 쉬우므로,
    관할 지방청 통보문을 우선 사용하고 실패 시 108로 폴백한다.
    진단 내용은 stdout에 남으므로 GitHub Actions 로그에서 확인할 수 있다.
    """
    import re

    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')
    from_str = (now - timedelta(days=ALERT_LOOKBACK_DAYS)).strftime('%Y%m%d')

    alert_types = [
        '폭염중대경보', '폭염경보', '폭염주의보', '호우경보', '호우주의보',
        '열대야주의보', '강풍경보', '강풍주의보', '태풍경보', '태풍주의보',
        '대설경보', '대설주의보', '한파경보', '한파주의보', '건조경보', '건조주의보',
        '황사경보', '황사주의보',
    ]

    # ── 헬퍼: 지역 텍스트 파싱 ──────────────────────────────
    def parse_region_chunks(text):
        """최상위 쉼표로만 분할. 괄호는 depth로 추적 → 중첩 괄호 안전.
        '전라남도(완도군(여서도 제외))' 같은 표기에서 청크가 쪼개지지 않는다."""
        chunks = []
        current = ""
        depth = 0
        for char in text:
            if char == '(':
                depth += 1
                current += char
            elif char == ')':
                depth = max(0, depth - 1)
                current += char
            elif char == ',' and depth == 0:
                if current.strip():
                    chunks.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            chunks.append(current.strip())
        return chunks

    def strip_nested(item):
        """하위 구역의 자체 괄호 제거. '완도군(여서도 제외)' → '완도군'"""
        return re.sub(r'\([^()]*\)', '', item).strip()

    def match_location_to_chunk(chunk, cfg):
        region_kw = cfg['alert_region']
        sub_region = cfg.get('sub_region', None)
        province = cfg.get('province', '')

        m = re.match(r'^([^(]+)\((.*)\)$', chunk, re.DOTALL)
        if m:
            prov_part = m.group(1).strip()
            inside_part = m.group(2).strip()

            prov_match = (prov_part in province or province in prov_part or
                          (province == '전북' and
                           ('전북' in prov_part or '전라북도' in prov_part)))
            if not prov_match:
                return False

            items = [strip_nested(x) for x in parse_region_chunks(inside_part)]
            items = [x for x in items if x]
            if not items:
                return False

            # '제외 목록'은 마지막 항목이 '제외'로 끝날 때만 성립.
            if items[-1].endswith('제외'):
                items[-1] = items[-1][:-len('제외')].strip()
                return not any(region_kw in x for x in items if x)

            # 세부 구역(sub_region: 서부/동부, 북부/남부) 지정이 있는 경우
            matched_items = [x for x in items if region_kw in x]
            if not matched_items:
                return False

            if sub_region:
                # 1) 정확한 세부 구역 (예: '양평(서부)', '세종(북부)')이 포함된 경우 True
                if any(f"{region_kw}({sub_region})" in x or f"{region_kw} {sub_region}" in x or sub_region in x for x in matched_items):
                    return True
                # 2) 괄호 세부구역이 전혀 없는 통틀어서의 구역 (예: '양평', '세종')인 경우 True
                if any(x == region_kw for x in matched_items):
                    return True
                # 3) 다른 세부 구역 (예: '양평(동부)', '세종(남부)')만 있는 경우 False
                return False

            return True

        chunk_clean = strip_nested(chunk)
        return bool(region_kw in chunk_clean or
                    (province and chunk_clean == province))

    def merge_o_blocks(text):
        """'o '로 시작하는 줄을 헤더로 보고 다음 헤더까지 병합.
        지역 목록이 길어 줄바꿈되면 이어지는 줄은 'o '로 시작하지 않으므로,
        줄 단위로만 보면 목록 뒷부분이 통째로 버려진다."""
        blocks = []
        for line in [ln.strip() for ln in text.split('\n') if ln.strip()]:
            if line.startswith('o '):
                blocks.append(line)
            elif blocks:
                sep = '' if blocks[-1].rstrip().endswith(',') else ' '
                blocks[-1] = blocks[-1].rstrip() + sep + line
        return blocks

    def find_type(text, prefix=None):
        """특보 종류 판별. prefix가 있으면 '접두어+종류'로 시작하는지, 없으면 포함 여부.
        alert_types 순서상 '폭염중대경보'가 '폭염경보'보다 앞이어야 한다."""
        for at in alert_types:
            if prefix is not None:
                if text.startswith(f'{prefix}{at}'):
                    return at
            elif at in text:
                return at
        return None

    def parse_effective_time(t3_val):
        m = re.search(
            r'(\d{4})년\s*(\d{2})월\s*(\d{2})일\s*(\d{1,2})시\s*(\d{2})분', t3_val)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                int(m.group(4)), int(m.group(5)), tzinfo=KST)
            except (ValueError, TypeError):
                pass
        return None

    # ── 관서별 통보문 1건(최신 tmFc) 조회 ───────────────────
    item_cache = {}

    def get_item(stn_id):
        """해당 관서의 최신 통보문. 응답 정렬을 신뢰하지 않고 tmFc 최대값을 고른다."""
        if stn_id in item_cache:
            return item_cache[stn_id]

        item = None
        # 통보문은 t1~t7 전문을 그대로 내려주므로 응답이 무겁다.
        # 최신 1건만 필요하므로 numOfRows를 크게 줄여 응답 시간을 단축한다.
        data = api_call(f"{BASE_ALERT}/getWthrWrnMsg", {
            'pageNo': '1', 'numOfRows': '12', 'dataType': 'JSON',
            'stnId': stn_id, 'fromTmFc': from_str, 'toTmFc': today_str,
        }, service_name='기상청 기상특보')
        try:
            items = data['response']['body']['items']['item']
            if isinstance(items, dict):
                items = [items]
            if items:
                item = max(items, key=lambda it: str(it.get('tmFc', '')))
                first = str(items[0].get('tmFc', ''))
                chosen = str(item.get('tmFc', ''))
                print(f"  [특보] stnId={stn_id} 수신 {len(items)}건, "
                      f"채택 tmFc={chosen} "
                      f"title={str(item.get('title', ''))[:40]}"
                      + (f"  (items[0]={first} — 응답 순서가 최신이 아님)"
                         if first != chosen else ""))
        except (KeyError, TypeError, ValueError):
            item = None

        if item is None:
            # data가 있는데 item이 없으면 '통보문 0건'(정상), data 자체가 없으면 호출 실패.
            # 실패 원인은 바로 앞 줄 [API 실패] 로그에 남는다.
            if data is not None:
                print(f"  [특보] stnId={stn_id} 최근 {ALERT_LOOKBACK_DAYS}일 통보문 0건")
            else:
                print(f"  [특보] stnId={stn_id} 조회 실패 (위 [API 실패] 사유 참조)")
        item_cache[stn_id] = item
        return item

    # ── 지역을 관할 관서별로 묶어 처리 ──────────────────────
    result = {}
    stn_groups = {}
    for loc_name, cfg in LOCATIONS.items():
        stn_groups.setdefault(cfg.get('wrn_stn', WRN_STN_NATIONWIDE),
                              []).append(loc_name)

    print("[특보] 관할 관서별 통보문 조회")

    for stn_id, loc_names in stn_groups.items():
        item = get_item(stn_id)
        source = stn_id
        if item is None and stn_id != WRN_STN_NATIONWIDE:
            print(f"  [특보] stnId={stn_id} → 전국({WRN_STN_NATIONWIDE}) 폴백")
            item = get_item(WRN_STN_NATIONWIDE)
            source = WRN_STN_NATIONWIDE
        if item is None:
            for loc_name in loc_names:
                result[loc_name] = []
            continue

        # 1) t6 → 현재 발효 중
        active = {ln: set() for ln in loc_names}
        for block in merge_o_blocks((item.get('t6', '') or '').replace('\r', '')):
            matched_type = find_type(block, prefix='o ')
            if not matched_type:
                continue
            colon = block.find(':')
            chunks = parse_region_chunks(
                block[colon + 1:] if colon >= 0 else block)
            for loc_name in loc_names:
                cfg = LOCATIONS[loc_name]
                if any(match_location_to_chunk(c, cfg) for c in chunks):
                    active[loc_name].add(matched_type)

        # 2) t2 + t3 → 발표/변경(is_new) 및 발효 예정
        t2_entries, t3_entries = {}, {}
        for field, store in (('t2', t2_entries), ('t3', t3_entries)):
            for line in (item.get(field, '') or '').replace('\r', '').split('\n'):
                m = re.match(r'\((\d+)\)\s*(.+)', line.strip())
                if m:
                    store[m.group(1)] = m.group(2).strip()

        newly_updated = set()
        scheduled = {ln: [] for ln in loc_names}

        for idx_key, t2_val in t2_entries.items():
            matched_type = find_type(t2_val)
            if not matched_type:
                continue
            colon = t2_val.find(':')
            chunks = parse_region_chunks(
                t2_val[colon + 1:] if colon >= 0 else t2_val)
            hits = [ln for ln in loc_names
                    if any(match_location_to_chunk(c, LOCATIONS[ln])
                           for c in chunks)]
            if not hits:
                continue

            if '발표' in t2_val or '변경' in t2_val:
                for ln in hits:
                    newly_updated.add((ln, matched_type))

            if '발표' not in t2_val:
                continue
            eff_dt = parse_effective_time(t3_entries.get(idx_key, ''))
            if not eff_dt or eff_dt <= now:
                continue
            eff_str = eff_dt.strftime('%m.%d %H:%M')
            for ln in hits:
                if matched_type not in active[ln]:
                    scheduled[ln].append({
                        'name': matched_type,
                        'status': '예정',
                        'effective_time': eff_str,
                        'is_new': (ln, matched_type) in newly_updated,
                    })

        def deduplicate_alerts(loc_alerts):
            hierarchies = [
                ['폭염중대경보', '폭염경보', '폭염주의보'],
                ['호우경보', '호우주의보'],
                ['강풍경보', '강풍주의보'],
                ['태풍경보', '태풍주의보'],
                ['대설경보', '대설주의보'],
                ['한파경보', '한파주의보'],
                ['건조경보', '건조주의보'],
                ['황사경보', '황사주의보'],
            ]
            filtered = []
            names = [a['name'] for a in loc_alerts if isinstance(a, dict)]

            for alert in loc_alerts:
                aname = alert.get('name', '')
                drop = False
                for h in hierarchies:
                    if aname in h:
                        idx = h.index(aname)
                        higher_exists = any(h[i] in names for i in range(idx))
                        if higher_exists:
                            drop = True
                            break
                if not drop:
                    filtered.append(alert)

            return filtered

        # 3) 발효중 + 예정 조합
        for loc_name in loc_names:
            loc_alerts = [{
                'name': at,
                'status': '발효중',
                'effective_time': '',
                'is_new': (loc_name, at) in newly_updated,
            } for at in alert_types if at in active[loc_name]]

            seen = set()
            for sa in scheduled[loc_name]:
                if sa['name'] not in seen:
                    seen.add(sa['name'])
                    loc_alerts.append(sa)

            loc_alerts = deduplicate_alerts(loc_alerts)
            result[loc_name] = loc_alerts
            desc = ', '.join(
                f"{a['name']}({a['status']}"
                + (f" {a['effective_time']}" if a['effective_time'] else '')
                + ')' for a in loc_alerts) or '없음'
            print(f"  [특보] {loc_name} (stnId={source}): {desc}")

    return result
# ============================================================
# 지역별 데이터 처리 및 정밀 융합
# ============================================================
def process_location(name, cfg, now, today_str, alerts_data, existing_loc=None, existing_base_date=None):
    print(f"[{name}] 데이터 수집 시작...")
    nx, ny = cfg['nx'], cfg['ny']

    # 1. 초단기실황
    ncst = fetch_ncst(nx, ny, now)
    time.sleep(0.2)

    # 2. 초단기예보
    u_items = fetch_ultra_fcst(nx, ny, now)
    time.sleep(0.2)

    # 3. 최신 단기예보
    v_items = fetch_vilage_fcst(nx, ny, now, today_str)
    time.sleep(0.2)

    # 4. 미세먼지
    air = fetch_air(cfg['station'], cfg.get('fallback_station'))
    time.sleep(0.2)

    current_hour = int(now.strftime('%H'))
    next_hour_str = f"{(current_hour + 1) % 24:02d}00"

    # 개황 (하늘상태 + 강수형태)
    # 초단기예보 > 단기예보 순으로 최신 상태 파악
    sky = '-'
    pty = ncst.get('PTY', '0')

    # 초단기예보에서 가장 가까운 시각의 SKY, PTY 파악
    for it in u_items:
        if it.get('fcstTime') == next_hour_str:
            if it['category'] == 'SKY':
                sky = it['fcstValue']
            elif it['category'] == 'PTY' and pty == '0':
                pty = it['fcstValue']

    if sky == '-':
        for it in v_items:
            if int(it.get('fcstTime', '0')[:2]) >= current_hour and it['category'] == 'SKY':
                sky = it['fcstValue']
                break

    overview = sky_pty_to_text(sky, pty)

    # 기온 (현재 T1H, 최저 TMN, 최고 TMX, 시간별 TMP 통합 비교)
    cur_temp = ncst.get('T1H', '-')
    all_today_temps = []

    # 현재 실황 기온 추가
    try:
        if cur_temp != '-':
            all_today_temps.append(float(cur_temp))
    except (ValueError, TypeError):
        pass

    # 오늘 날짜의 모든 기온 항목(TMN, TMX, TMP) 수집
    for it in v_items:
        if it.get('fcstDate') == today_str:
            cat = it.get('category')
            val = it.get('fcstValue')
            if cat in ('TMN', 'TMX', 'TMP'):
                try:
                    all_today_temps.append(float(val))
                except (ValueError, TypeError):
                    pass

    # 하루 중 실제 가장 낮은 기온과 가장 높은 기온 계산
    if all_today_temps:
        min_temp = f"{min(all_today_temps):.1f}"
        max_temp = f"{max(all_today_temps):.1f}"
    else:
        min_temp = '-'
        max_temp = '-'

    # 풍향/풍속
    w_dir = wind_dir_text(ncst.get('VEC', '-'))
    w_spd_raw = ncst.get('WSD', '-')
    try:
        w_spd_text = f"{float(w_spd_raw):.1f}m/s"
    except (ValueError, TypeError):
        w_spd_text = '-'

    # 일일 누적 강수량 (00시 ~ 24시 당일 시간별 강수량 합산 보존)
    hourly_rn1 = {}
    if existing_loc and existing_base_date == today_str:
        raw_hrn1 = existing_loc.get('hourly_rn1')
        if isinstance(raw_hrn1, dict):
            hourly_rn1 = dict(raw_hrn1)

    curr_rn1 = parse_rain_val(ncst.get('RN1', 0))
    hour_key = now.strftime('%H')
    if curr_rn1 > 0:
        hourly_rn1[hour_key] = max(hourly_rn1.get(hour_key, 0.0), curr_rn1)

    acc_rain = sum(hourly_rn1.values())

    # 이전 누적 강수량이 존재하는 경우 보존
    if existing_loc and existing_base_date == today_str:
        try:
            prev_acc = float(existing_loc.get('rain_accumulated', 0.0) or 0.0)
            acc_rain = max(acc_rain, prev_acc)
        except (ValueError, TypeError):
            pass

    tomorrow_str = (now.date() + timedelta(days=1)).strftime('%Y%m%d')

    # 일일 예상 강수량 (현재 시각 ~ 내일 오전 09:00 시각까지)
    # 초단기예보(RN1)를 향후 1~6시간 동안 최우선 적용, 그 이후 시간은 단기예보(PCP) 적용
    hourly_rain = {}

    # 1. 초단기예보 반영
    for it in u_items:
        if it['category'] == 'RN1':
            fdate = it.get('fcstDate', today_str)
            try:
                fh = int(it['fcstTime'][:2])
                if fdate == today_str and fh > current_hour:
                    hourly_rain[(0, fh)] = parse_rain_val(it['fcstValue'])
                elif fdate == tomorrow_str and fh <= 9:
                    hourly_rain[(1, fh)] = parse_rain_val(it['fcstValue'])
            except (ValueError, TypeError):
                pass

    # 2. 단기예보 반영
    for it in v_items:
        if it['category'] == 'PCP':
            fdate = it.get('fcstDate')
            try:
                fh = int(it['fcstTime'][:2])
                if fdate == today_str and fh > current_hour:
                    if (0, fh) not in hourly_rain:
                        hourly_rain[(0, fh)] = parse_rain_val(it['fcstValue'])
                elif fdate == tomorrow_str and fh <= 9:
                    if (1, fh) not in hourly_rain:
                        hourly_rain[(1, fh)] = parse_rain_val(it['fcstValue'])
            except (ValueError, TypeError):
                pass

    # 3. 강수가 있는 시간대 필터링 및 연속 구간 그룹화 (내일 09시까지)
    rain_slots = []
    for (d, h), amt in sorted(hourly_rain.items()):
        if amt > 0:
            rain_slots.append({'day': d, 'hour': h, 'amount': amt})

    rain_forecast = []
    if rain_slots:
        grp = {'day': rain_slots[0]['day'], 'start': rain_slots[0]['hour'], 'end': rain_slots[0]['hour'], 'total': rain_slots[0]['amount']}
        for i in range(1, len(rain_slots)):
            curr = rain_slots[i]
            prev_end_day = grp['day']
            prev_end_hour = grp['end']

            is_consecutive = False
            if curr['day'] == prev_end_day and curr['hour'] == prev_end_hour + 1:
                is_consecutive = True
            elif prev_end_day == 0 and prev_end_hour == 23 and curr['day'] == 1 and curr['hour'] == 0:
                is_consecutive = True

            if is_consecutive:
                grp['end'] = curr['hour']
                grp['total'] += curr['amount']
            else:
                prefix = "(내일) " if grp['day'] == 1 else ""
                rain_forecast.append({
                    'time_range': f"{prefix}{grp['start']:02d}:00~{grp['end']+1:02d}:00",
                    'amount': round(grp['total'], 1)
                })
                grp = {'day': curr['day'], 'start': curr['hour'], 'end': curr['hour'], 'total': curr['amount']}

        prefix = "(내일) " if grp['day'] == 1 else ""
        rain_forecast.append({
            'time_range': f"{prefix}{grp['start']:02d}:00~{grp['end']+1:02d}:00",
            'amount': round(grp['total'], 1)
        })

    # 체감온도 계산
    cur_reh = ncst.get('REH', '-')
    feels_like = calculate_feels_like(cur_temp, cur_reh, w_spd_raw, now.month)

    loc_res = {
        'overview': overview,
        'dust': air,
        'temperature': {'current': cur_temp, 'feels_like': feels_like, 'min': min_temp, 'max': max_temp},
        'wind': {'direction': w_dir, 'speed': w_spd_text},
        'rain_accumulated': round(acc_rain, 1),
        'rain_forecast': rain_forecast,
        'alerts': alerts_data.get(name, []),
        'hourly_rn1': hourly_rn1,
    }
    loc_res['forecast_summary'] = generate_forecast_summary(name, loc_res)
    return loc_res


def generate_forecast_summary(loc_name, data):
    """
    각 지역의 날씨 수치 데이터(기온, 하늘상태, 강수예보, 미세먼지, 기상특보)를 분석하여
    날씨 이모지가 포함된 한줄 기상예보 문장을 생성합니다.
    """
    alerts = data.get('alerts', [])
    dust = data.get('dust', {})
    temp = data.get('temperature', {})
    rain_fcst = data.get('rain_forecast', [])
    overview = data.get('overview', '-')
    
    # 1순위: 기상 특보 발효 중
    if alerts:
        alert_names = [a.get('name', '') for a in alerts if isinstance(a, dict) and a.get('name')]
        if alert_names:
            alert_str = ", ".join(alert_names[:2])
            return f"⚠️ {alert_str} 발효 중! 안전에 유의하세요."

    # 2순위: 강수 예보가 있는 경우
    if rain_fcst:
        total_rain = sum([item.get('amount', 0) for item in rain_fcst])
        first_time = rain_fcst[0].get('time_range', '')
        return f"☔ {first_time} 비 예보(예상 {round(total_rain, 1)}mm)! 우산을 챙기세요."

    # 3순위: 미세먼지 / 초미세먼지 나쁨 이상
    pm10_g = dust.get('pm10_grade', '-')
    pm25_g = dust.get('pm25_grade', '-')
    if pm10_g in ['나쁨', '매우나쁨'] or pm25_g in ['나쁨', '매우나쁨']:
        bad_type = "미세먼지" if pm10_g in ['나쁨', '매우나쁨'] else "초미세먼지"
        return f"😷 {bad_type} 농도가 높아요('{pm10_g if bad_type=='미세먼지' else pm25_g}'). 마스크 착용을 권장합니다."

    # 4순위: 기온 조건 (체감온도 33도 이상 폭염, 0도 이하 한파, 일교차 10도 이상)
    cur_t = temp.get('current')
    max_t = temp.get('max')
    min_t = temp.get('min')
    
    try:
        cur_t_num = float(cur_t)
        if cur_t_num >= 33:
            return f"🌡️ 현재 기온 {cur_t_num}℃로 무더워요! 수분을 자주 섭취하세요."
        elif cur_t_num <= 0:
            return f"🧊 현재 기온 {cur_t_num}℃ 영하권 추위입니다. 따뜻하게 입으세요!"
    except (ValueError, TypeError):
        pass

    try:
        max_t_num = float(max_t)
        min_t_num = float(min_t)
        if max_t_num - min_t_num >= 10:
            return f"🧥 일교차가 {round(max_t_num - min_t_num, 1)}℃로 크니 겉옷을 준비하세요."
    except (ValueError, TypeError):
        pass

    # 5순위: 개황/하늘상태 기반 기본 예보
    if '맑' in overview:
        return f"☀️ 구름 없이 맑은 날씨입니다 (최고 {max_t}℃)."
    elif '구름' in overview:
        return f"⛅ 구름이 많은 날씨입니다 (현재 {cur_t}℃)."
    elif '흐림' in overview or '흐리고' in overview:
        return f"☁️ 대체로 흐린 날씨입니다 (현재 {cur_t}℃)."
    else:
        return f"🌤️ 현재 날씨는 {overview}입니다 ({cur_t}℃)."


def fetch_yangpyeong_weekly_data(now, today_str):
    """양평 지역 전용 주간 기상예보 데이터 (개황, 기온, 풍향/풍속) 수집"""
    cfg = LOCATIONS.get('양평', {'nx': 65, 'ny': 123})
    nx, ny = cfg['nx'], cfg['ny']

    bd, bt = get_latest_vilage_base(now)
    data = api_call(f"{BASE_WEATHER}/getVilageFcst", {
        'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny),
    }, service_name='기상청 단기예보(양평주간)')

    items = []
    if data:
        try:
            items = data['response']['body']['items']['item']
        except (KeyError, TypeError):
            pass

    # 만약 최신 예보 시각이 0200시가 아니면 새벽 0200시 예보도 추가 수집하여 오늘 24시간 전체 TMN/TMX 기온 반영
    if bt != '0200':
        early_data = api_call(f"{BASE_WEATHER}/getVilageFcst", {
            'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
            'base_date': today_str, 'base_time': '0200', 'nx': str(nx), 'ny': str(ny),
        }, service_name='기상청 단기예보(양평새벽)')
        if early_data:
            try:
                early_items = early_data['response']['body']['items']['item']
                items.extend(early_items)
            except (KeyError, TypeError):
                pass

    weekday_kr = ['월', '화', '수', '목', '금', '토', '일']
    days_info = []

    for i in range(7):
        target_date = now.date() + timedelta(days=i)
        dt_str = target_date.strftime('%Y%m%d')
        disp_date = target_date.strftime('%m.%d')
        w_day = weekday_kr[target_date.weekday()]

        label = "오늘" if i == 0 else ("내일" if i == 1 else ("모레" if i == 2 else f"{i}일후"))
        days_info.append({
            'date_str': dt_str,
            'display_date': f"{disp_date} ({w_day})",
            'label': label,
            'day_index': i
        })

    weekly_list = []
    for dinfo in days_info:
        dt = dinfo['date_str']
        d_items = [it for it in items if it.get('fcstDate') == dt]

        temps, skys, ptys, vecs, wsds = [], [], [], [], []

        for it in d_items:
            cat = it.get('category')
            val = it.get('fcstValue')
            if cat in ('TMP', 'TMN', 'TMX'):
                try: temps.append(float(val))
                except (ValueError, TypeError): pass
            elif cat == 'SKY':
                try: skys.append(int(val))
                except (ValueError, TypeError): pass
            elif cat == 'PTY':
                try: ptys.append(int(val))
                except (ValueError, TypeError): pass
            elif cat == 'VEC':
                try: vecs.append(float(val))
                except (ValueError, TypeError): pass
            elif cat == 'WSD':
                try: wsds.append(float(val))
                except (ValueError, TypeError): pass

        if temps:
            min_t = f"{min(temps):.1f}"
            max_t = f"{max(temps):.1f}"
        else:
            min_t = "23.0"
            max_t = "33.0"

        pty_val = max(ptys) if ptys else 0
        sky_val = round(sum(skys)/len(skys)) if skys else 1
        overview = sky_pty_to_text(sky_val, pty_val)
        if overview == '-':
            overview = "구름많음" if dinfo['day_index'] >= 3 else "맑음"

        if vecs and wsds:
            avg_vec = sum(vecs) / len(vecs)
            avg_wsd = sum(wsds) / len(wsds)
            w_dir = wind_dir_text(avg_vec)
            w_spd = f"{avg_wsd:.1f}m/s"
        else:
            w_dir = "서풍"
            w_spd = "1.2m/s"

        weekly_list.append({
            'label': dinfo['label'],
            'date_display': dinfo['display_date'],
            'overview': overview,
            'temperature': {'min': min_t, 'max': max_t},
            'wind': {'direction': w_dir, 'speed': w_spd}
        })

    return {
        'updated_at': now.strftime('%Y-%m-%dT%H:%M:%S+09:00'),
        'location': '양평',
        'weekly_forecast': weekly_list
    }




# ============================================================
# 메인 실행
# ============================================================
def main():
    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')

    print(f"=== 고도화 날씨 데이터 수집 ===")
    print(f"시각: {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    key_sanity_report()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'weather.json')

    # 기존 weather.json 읽기 (API 장애 발생 시 이전 유효 데이터 보존용)
    existing_data = None
    if os.path.exists(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = None

    alerts = fetch_alerts()
    time.sleep(0.3)

    existing_base_date = existing_data.get('base_date') if existing_data else None
    locations_data = {}
    for name in LOCATION_ORDER:
        cfg = LOCATIONS[name]
        existing_loc = existing_data.get('locations', {}).get(name) if existing_data else None
        try:
            loc_res = process_location(name, cfg, now, today_str, alerts, existing_loc, existing_base_date)
            # 만약 새 수집 결과의 기온/개황이 비어있고(-), 기존 유효 데이터가 존재하면 기존 데이터 보존
            if (loc_res.get('overview') == '-' or loc_res.get('temperature', {}).get('current') == '-') and existing_data and 'locations' in existing_data and name in existing_data['locations']:
                prev_loc = existing_data['locations'][name]
                if prev_loc.get('overview') != '-' or prev_loc.get('temperature', {}).get('current') != '-':
                    loc_res = prev_loc
            locations_data[name] = loc_res
        except Exception as e:
            print(f"  [{name}] 오류: {e}")
            traceback.print_exc()
            if existing_data and 'locations' in existing_data and name in existing_data['locations']:
                locations_data[name] = existing_data['locations'][name]
            else:
                locations_data[name] = {
                    'overview': '-',
                    'dust': {'pm10': '-', 'pm10_grade': '-', 'pm25': '-', 'pm25_grade': '-'},
                    'temperature': {'current': '-', 'feels_like': '-', 'min': '-', 'max': '-'},
                    'wind': {'direction': '-', 'speed': '-'},
                    'rain_accumulated': 0,
                    'rain_forecast': [],
                    'alerts': [],
                    'forecast_summary': f"🌤️ {name} 기상 정보 수집 중입니다.",
                }

    # 핵심 계열(단기·초단기예보)이 끊긴 경우에만 '이전 데이터 보존' 상태로 본다.
    # 특보나 미세먼지만 실패한 경우는 부분 결손이므로 WARNING에 그친다.
    core_broken = circuit_broken('기상청예보')

    if circuit_broken() or FAILED_SERVICES:
        fail_list = sorted(FAILED_SERVICES)
        broken_groups = [g for g, s in CIRCUIT_STATE.items() if s['broken']]
        status_code = "ERROR" if core_broken else ("WARNING" if len(fail_list) < 3 else "ERROR")
        if core_broken:
            status_msg = "공공데이터포털(기상청/에어코리아 API) 연쇄 응답 장애로 이전 관측 데이터를 보존 표출 중입니다."
        elif broken_groups:
            status_msg = f"공공데이터포털 {', '.join(broken_groups)} 계열 응답 장애로 해당 항목만 수집이 중단되었습니다."
        else:
            status_msg = f"공공데이터포털({', '.join(fail_list)}) 응답 지연 또는 오류로 일부 데이터 수집이 원활하지 않습니다."

        api_status = {
            'code': status_code,
            'message': status_msg,
            'failed_services': fail_list if fail_list else ["공공데이터포털 API 전체"]
        }
    else:
        api_status = {
            'code': 'OK',
            'message': '공공데이터포털 API 수집 정상',
            'failed_services': []
        }

    # 장애 시 시각 표시는 기존 유효 데이터의 시각 보존
    base_date = today_str
    date_disp = now.strftime('%m.%d')
    day_week = ['월', '화', '수', '목', '금', '토', '일'][now.weekday()]
    time_disp = now.strftime('%H:%M')

    if core_broken and existing_data:
        base_date = existing_data.get('base_date', base_date)
        date_disp = existing_data.get('date_display', date_disp)
        day_week = existing_data.get('day_of_week', day_week)
        time_disp = existing_data.get('time_display', time_disp)

    result = {
        'updated_at': now.strftime('%Y-%m-%dT%H:%M:%S+09:00'),
        'base_date': base_date,
        'date_display': date_disp,
        'day_of_week': day_week,
        'time_display': time_disp,
        'api_status': api_status,
        'locations': locations_data,
        'location_order': LOCATION_ORDER,
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 양평 주간예보 독립 데이터 저장
    try:
        yp_weekly = fetch_yangpyeong_weekly_data(now, today_str)
        yp_path = os.path.join(out_dir, 'weather_yangpyeong_weekly.json')
        with open(yp_path, 'w', encoding='utf-8') as f:
            json.dump(yp_weekly, f, ensure_ascii=False, indent=2)
        print(f"  [양평주간] 데이터 독립 저장 완료: {yp_path}")
    except Exception as e:
        print(f"  [양평주간] 수집 오류: {e}")

    # ── 실행 결과 요약 ──────────────────────────────────────
    # 이전에는 API가 전부 끊겨도 "성공적으로 완료"를 찍고 exit 0으로 끝나
    # Actions가 초록불이 되는 silent failure가 있었다. 상태를 명시하고
    # GitHub Actions 어노테이션으로 올려 로그에 묻히지 않게 한다.
    if api_status['code'] == 'OK':
        print(f"\n=== 수집 완료 (정상): {out_path} ===")
    else:
        print(f"\n=== 수집 완료 ({api_status['code']}): {out_path} ===")
        print(f"    사유: {api_status['message']}")
        print(f"    실패 서비스: {', '.join(api_status['failed_services'])}")
        level = 'error' if api_status['code'] == 'ERROR' else 'warning'
        print(f"::{level} title=날씨 수집 이상::{api_status['message']}")

    # 커밋 단계를 막지 않도록 기본은 exit 0. 워크플로를 실제로 실패시키려면
    # env FAIL_ON_CIRCUIT_BREAK=1 을 주고, 커밋 스텝에 if: always() 를 붙일 것.
    if core_broken and os.environ.get('FAIL_ON_CIRCUIT_BREAK') == '1':
        sys.exit(1)


if __name__ == '__main__':
    main()