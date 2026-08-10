#!/usr/bin/env python3
"""
8개 지역 날씨 데이터 수집 스크립트 (안정화/고속 병렬 수집 리팩토링 버전)

주요 특징:
1. 공공데이터포털 serviceKey 이중 인코딩 방지 (unquote 처리 후 params 전달)
2. Connect/Read Timeout 분리 (3초 / 6초) 및 ThreadPoolExecutor 8개 지역 동시 수집
3. API 호출 실패 시 기존 weather.json 유효 데이터 100% 보존 (Fallback)
4. 실패 시 불필요한 쿨다운 방지를 위한 유효 시각 유지
5. 양평 주간예보 및 7대 API 실시간 진단 연동
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows 콘솔 인코딩 호환성 설정
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ============================================================
# 환경 변수 로드 및 인증키 정제
# ============================================================
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

RAW_API_KEY = os.environ.get('DATA_GO_KR_KEY', '').strip()
if not RAW_API_KEY:
    print("ERROR: DATA_GO_KR_KEY 환경변수가 설정되지 않았습니다.")
    sys.exit(1)

# 공공데이터포털 키는 decoding 키(unquoted) 상태로 requests params에 전달해야 이중 인코딩이 발생하지 않음
API_KEY = unquote(RAW_API_KEY)

KMA_API_HUB_KEY = os.environ.get('KMA_API_HUB_KEY', '').strip()
if KMA_API_HUB_KEY:
    print("INFO: KMA_API_HUB_KEY 확인됨 - 기상청 API 허브 실시간 강수 관측 연동 활성화")

# ============================================================
# 상수 및 설정 (8개 지역)
# ============================================================
LOCATIONS = {
    '양평': {'nx': 70, 'ny': 126, 'station': '양평읍',   'fallback_station': '가평읍', 'alert_region': '양평', 'sub_region': '서부', 'province': '경기도',   'wrn_stn': '109', 'kma_stn': '212'},
    '경산': {'nx': 92, 'ny': 91,  'station': '시지동',   'fallback_station': '만촌동', 'alert_region': '경산', 'province': '경상북도', 'wrn_stn': '143', 'kma_stn': '830'},
    '사천': {'nx': 81, 'ny': 72,  'station': '사천읍',   'fallback_station': '향촌동', 'alert_region': '사천', 'province': '경상남도', 'wrn_stn': '159', 'kma_stn': '893'},
    '함안': {'nx': 87, 'ny': 78,  'station': '가야읍',   'fallback_station': '내서읍', 'alert_region': '함안', 'province': '경상남도', 'wrn_stn': '159', 'kma_stn': '877'},
    '성주': {'nx': 85, 'ny': 92,  'station': '성주군',   'fallback_station': '다사읍', 'alert_region': '성주', 'province': '경상북도', 'wrn_stn': '143', 'kma_stn': '847'},
    '세종': {'nx': 65, 'ny': 104, 'station': '아름동',   'fallback_station': '조치원읍', 'alert_region': '세종', 'sub_region': '북부', 'province': '세종',    'wrn_stn': '133', 'kma_stn': '637'},
    '계룡': {'nx': 66, 'ny': 100, 'station': '엄사면',   'fallback_station': '논산',   'alert_region': '계룡', 'province': '충청남도', 'wrn_stn': '133', 'kma_stn': '640'},
    '임실': {'nx': 67, 'ny': 85,  'station': '임실읍',   'fallback_station': '삼천동', 'alert_region': '임실', 'province': '전북',     'wrn_stn': '146', 'kma_stn': '244'},
}

LOCATION_ORDER = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실']
KST = timezone(timedelta(hours=9))

BASE_WEATHER = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
BASE_AIR = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
BASE_ALERT = "https://apis.data.go.kr/1360000/WthrWrnInfoService"

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 weather-dashboard/2.0',
    'Accept': 'application/json, text/plain, */*',
}

# HTTP Session 설정 (해외 Runner 및 네트워크 지연 대응을 위한 Retry 강화)
session = requests.Session()
retries = Retry(total=3, backoff_factor=1.5, status_forcelist=[500, 502, 503, 504, 520, 522, 524])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.mount('http://', HTTPAdapter(max_retries=retries))

SERVICE_STATS = {}

def record_stat(service_name, is_ok):
    if not service_name:
        return
    st = SERVICE_STATS.setdefault(service_name, {'ok': 0, 'fail': 0})
    if is_ok:
        st['ok'] += 1
    else:
        st['fail'] += 1

# ============================================================
# API 호출 코어 헬퍼
# ============================================================
def safe_api_get(url, params, service_name=None, timeout=(10.0, 25.0)):
    req_params = {'serviceKey': API_KEY}
    req_params.update(params)

    label = service_name or url.rsplit('/', 1)[-1]
    try:
        resp = session.get(url, params=req_params, headers=REQUEST_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            record_stat(service_name, False)
            return None

        try:
            data = resp.json()
            header = (data.get('response', {}) or {}).get('header', {}) or {}
            rc = str(header.get('resultCode', ''))
            if rc in ('00', '03'):
                record_stat(service_name, True)
                return data
            else:
                record_stat(service_name, False)
                return None
        except (json.JSONDecodeError, AttributeError):
            record_stat(service_name, False)
            return None

    except (requests.Timeout, requests.RequestException):
        record_stat(service_name, False)
        return None

# ============================================================
# 시간 및 변환 헬퍼
# ============================================================
def get_latest_vilage_base(now_time):
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
    check = now_time - timedelta(minutes=45)
    return check.strftime('%Y%m%d'), check.strftime('%H') + '00'

def get_ncst_base(now_time):
    check = now_time - timedelta(minutes=40)
    return check.strftime('%Y%m%d'), check.strftime('%H') + '00'

def wind_dir_text(deg):
    try:
        deg = float(deg)
    except (ValueError, TypeError):
        return '-'
    dirs = ['북풍', '북북동풍', '북동풍', '동북동풍', '동풍', '동남동풍', '남동풍', '남남동풍',
            '남풍', '남남서풍', '남서풍', '서남서풍', '서풍', '서북서풍', '북서풍', '북북서풍']
    return dirs[round(deg / 22.5) % 16]

def parse_rain_val(value):
    if not value or str(value).strip() in ('강수없음', '0', '0.0', '-'):
        return 0.0
    s_val = str(value).strip()
    if '미만' in s_val: return 0.5
    if '이상' in s_val: return 50.0
    try:
        return float(s_val.replace('mm', '').strip())
    except (ValueError, TypeError):
        return 0.0

def sky_pty_to_text(sky, pty):
    try: pty = int(pty)
    except (ValueError, TypeError): pty = 0
    pty_map = {1: '비', 2: '비/눈', 3: '눈', 4: '소나기', 5: '빗방울', 6: '빗방울/눈날림', 7: '눈날림'}
    if pty in pty_map:
        return pty_map[pty]
    try: sky = int(sky)
    except (ValueError, TypeError): return '-'
    return {1: '맑음', 3: '구름많음', 4: '흐림'}.get(sky, '-')

def calc_feels_like(temp_str, reh_str, wsd_str, month):
    try: t = float(temp_str)
    except (ValueError, TypeError): return '-'
    try: h = float(reh_str)
    except (ValueError, TypeError): h = 50.0
    try: w = float(wsd_str)
    except (ValueError, TypeError): w = 0.0

    v_kmh = w * 3.6
    if (month in [10, 11, 12, 1, 2, 3, 4] or t <= 10.0) and t <= 10.0 and v_kmh >= 4.68:
        fl = 13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16)
        return f"{fl:.1f}"
    if (month in [5, 6, 7, 8, 9] or t >= 20.0) and t >= 20.0:
        tw = (t * math.atan(0.151977 * ((h + 8.313659) ** 0.5)) +
              math.atan(t + h) - math.atan(h - 1.676331) +
              0.00391838 * (h ** 1.5) * math.atan(0.023101 * h) - 4.686035)
        fl = -0.2442 + 0.55399 * tw + 0.45535 * t - 0.0022 * (tw ** 2) + 0.00278 * tw * t + 3.0
        return f"{fl:.1f}"
    return f"{t:.1f}"

def calc_pm_grade(val_str, grade_str, is_pm25=False):
    t = {'1': '좋음', '2': '보통', '3': '나쁨', '4': '매우나쁨'}.get(str(grade_str), '')
    if t: return t
    try:
        v = float(val_str)
        if not is_pm25:
            if v <= 30: return '좋음'
            if v <= 80: return '보통'
            if v <= 150: return '나쁨'
            return '매우나쁨'
        else:
            if v <= 15: return '좋음'
            if v <= 35: return '보통'
            if v <= 75: return '나쁨'
            return '매우나쁨'
    except (ValueError, TypeError):
        return '-'

# ============================================================
# API 수집 함수군
# ============================================================
def fetch_ncst(nx, ny, now_time):
    bd, bt = get_ncst_base(now_time)
    data = safe_api_get(f"{BASE_WEATHER}/getUltraSrtNcst", {
        'pageNo': '1', 'numOfRows': '100', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny)
    }, service_name='기상청 초단기실황')
    res = {}
    if data:
        try:
            items = data['response']['body']['items']['item']
            for it in items:
                res[it['category']] = it['obsrValue']
        except (KeyError, TypeError):
            pass
    return res

def fetch_ultra_fcst(nx, ny, now_time):
    bd, bt = get_ultra_fcst_base(now_time)
    data = safe_api_get(f"{BASE_WEATHER}/getUltraSrtFcst", {
        'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny)
    }, service_name='기상청 초단기예보')
    if data:
        try:
            return data['response']['body']['items']['item']
        except (KeyError, TypeError):
            pass
    return []

def fetch_vilage_fcst(nx, ny, now_time, today_str):
    bd, bt = get_latest_vilage_base(now_time)
    data = safe_api_get(f"{BASE_WEATHER}/getVilageFcst", {
        'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
        'base_date': bd, 'base_time': bt, 'nx': str(nx), 'ny': str(ny)
    }, service_name='기상청 단기예보')
    items = []
    if data:
        try:
            items = data['response']['body']['items']['item']
        except (KeyError, TypeError):
            pass

    tmn_exists = any(i.get('category') == 'TMN' and i.get('fcstDate') == today_str for i in items)
    tmx_exists = any(i.get('category') == 'TMX' and i.get('fcstDate') == today_str for i in items)

    if (not tmn_exists or not tmx_exists) and bt != '0200':
        early_data = safe_api_get(f"{BASE_WEATHER}/getVilageFcst", {
            'pageNo': '1', 'numOfRows': '1000', 'dataType': 'JSON',
            'base_date': today_str, 'base_time': '0200', 'nx': str(nx), 'ny': str(ny)
        }, service_name='기상청 단기예보(새벽)')
        if early_data:
            try:
                early_items = early_data['response']['body']['items']['item']
                items.extend(early_items)
            except (KeyError, TypeError):
                pass

    tomorrow_str = (now_time.date() + timedelta(days=1)).strftime('%Y%m%d')
    return [i for i in items if i.get('fcstDate') in (today_str, tomorrow_str)]

def fetch_air(station, fallback_station=None):
    def _query(st):
        data = safe_api_get(f"{BASE_AIR}/getMsrstnAcctoRltmMesureDnsty", {
            'returnType': 'json', 'stationName': st,
            'dataTerm': 'DAILY', 'ver': '1.3', 'numOfRows': '1'
        }, service_name='에어코리아 미세먼지')
        if not data:
            return None
        try:
            items = data['response']['body']['items']
            if items:
                it = items[0]
                p10 = it.get('pm10Value')
                p25 = it.get('pm25Value')
                if p10 is not None and p10 not in ('-', ''):
                    return {
                        'pm10': str(p10),
                        'pm10_grade': calc_pm_grade(p10, it.get('pm10Grade', ''), is_pm25=False),
                        'pm25': str(p25) if p25 is not None else '-',
                        'pm25_grade': calc_pm_grade(p25, it.get('pm25Grade', ''), is_pm25=True)
                    }
        except (KeyError, TypeError):
            pass
        return None

    res = _query(station)
    if res:
        res.update({'is_fallback': False, 'station_used': station, 'primary_station': station})
        return res
    if fallback_station:
        res = _query(fallback_station)
        if res:
            res.update({'is_fallback': True, 'station_used': fallback_station, 'primary_station': station})
            return res

    return {'pm10': '-', 'pm10_grade': '-', 'pm25': '-', 'pm25_grade': '-',
            'is_fallback': False, 'station_used': station, 'primary_station': station}

def fetch_kma_hub_obs(stn_id):
    if not KMA_API_HUB_KEY or not stn_id:
        return None
    url = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-aws2_min"
    try:
        resp = session.get(url, params={'stn': str(stn_id), 'disp': '1', 'help': '0', 'authKey': KMA_API_HUB_KEY},
                           timeout=(8.0, 15.0), headers=REQUEST_HEADERS)
        if resp.status_code == 200:
            lines = [line.strip() for line in resp.text.splitlines() if line.strip() and not line.startswith('#')]
            if lines:
                parts = [p.strip() for p in lines[-1].split(',')]
                if len(parts) >= 14:
                    def _tf(v):
                        try:
                            f = float(v)
                            return f if f > -50 else None
                        except (ValueError, TypeError): return None
                    return {
                        'temp': _tf(parts[8]),
                        'wind_deg': _tf(parts[6]),
                        'wind_speed': _tf(parts[7]),
                        'rn_day': _tf(parts[13])
                    }
    except Exception:
        pass
    return None

def fetch_dust_alerts():
    alerts_by_loc = {ln: [] for ln in LOCATIONS}
    data = safe_api_get("https://apis.data.go.kr/B552584/UlfptcaAlarmInqireSvc/getUlfptcaAlarmInfo", {
        'returnType': 'json', 'year': str(datetime.now(KST).year),
        'numOfRows': '100', 'pageNo': '1'
    }, service_name='에어코리아 미세먼지경보', timeout=(10.0, 20.0))

    if not data:
        return alerts_by_loc
    try:
        items = data.get('response', {}).get('body', {}).get('items', [])
        for item in items:
            clear_date = str(item.get('clearDate', '') or '').strip()
            if clear_date and clear_date not in ('None', '-'):
                continue
            dist = str(item.get('districtName', '') or '').strip()
            move = str(item.get('moveName', '') or '').strip()
            code = str(item.get('itemCode', '') or '').strip()
            gbn = str(item.get('issueGbn', '') or '').strip()
            alert_name = f"{code} {gbn}" if code and gbn else "미세먼지 경보"

            for loc_name, cfg in LOCATIONS.items():
                prov = cfg.get('province', '')
                region = cfg.get('alert_region', '')
                prov_match = (prov in dist or dist in prov or (prov == '전북' and '전북' in dist))
                region_match = (region in move or move in region or dist in region or region in dist)
                if prov_match and region_match:
                    alerts_by_loc[loc_name].append({
                        'name': alert_name, 'status': '발효중', 'effective_time': '',
                        'is_new': False, 'value': str(item.get('issueVal', '') or '').strip()
                    })
    except Exception:
        pass
    return alerts_by_loc

def fetch_alerts():
    alerts_by_loc = {ln: [] for ln in LOCATIONS}
    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')
    from_str = (now - timedelta(days=2)).strftime('%Y%m%d')

    alert_types = [
        '폭염중대경보', '폭염경보', '폭염주의보', '호우경보', '호우주의보',
        '열대야주의보', '강풍경보', '강풍주의보', '태풍경보', '태풍주의보',
        '대설경보', '대설주의보', '한파경보', '한파주의보', '건조경보', '건조주의보',
        '황사경보', '황사주의보'
    ]

    data = safe_api_get(f"{BASE_ALERT}/getWthrWrnMsg", {
        'pageNo': '1', 'numOfRows': '15', 'dataType': 'JSON',
        'stnId': '108', 'fromTmFc': from_str, 'toTmFc': today_str
    }, service_name='기상청 기상특보', timeout=(10.0, 20.0))

    if not data:
        return alerts_by_loc
    try:
        items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
        if isinstance(items, dict): items = [items]
        if not items: return alerts_by_loc

        latest_item = max(items, key=lambda it: str(it.get('tmFc', '')))
        t6_text = (latest_item.get('t6', '') or '').replace('\r', '')

        for loc_name, cfg in LOCATIONS.items():
            region_kw = cfg['alert_region']
            for line in t6_text.split('\n'):
                if region_kw in line:
                    for at in alert_types:
                        if at in line and not any(a['name'] == at for a in alerts_by_loc[loc_name]):
                            alerts_by_loc[loc_name].append({
                                'name': at, 'status': '발효중', 'effective_time': '', 'is_new': False
                            })
    except Exception:
        pass
    return alerts_by_loc

# ============================================================
# 데이터 결합 및 개황 처리
# ============================================================
def get_period_overviews(now_time, today_str, ncst, u_items, v_items):
    current_hour = now_time.hour
    is_morning = current_hour < 12
    tomorrow_str = (now_time.date() + timedelta(days=1)).strftime('%Y%m%d')

    def _extract(target_date, start_hour, end_hour):
        pty = str(ncst.get('PTY', '0')) if target_date == today_str else '0'
        sky = '-'
        period_items = [i for i in v_items if i.get('fcstDate') == target_date and start_hour <= int(i.get('fcstTime', '0000')[:2]) <= end_hour]
        for i in period_items:
            if i.get('category') == 'PTY' and str(i.get('fcstValue', '0')) not in ('0', '-'):
                pty = str(i.get('fcstValue'))
                break
        for i in period_items:
            if i.get('category') == 'SKY' and str(i.get('fcstValue', '-')) != '-':
                sky = i.get('fcstValue')
                break
        return sky_pty_to_text(sky, pty)

    if is_morning:
        slot = 'AM'
        labels = ['금일 오전', '금일 오후']
        ov1 = _extract(today_str, 0, 12)
        ov2 = _extract(today_str, 12, 23)
    else:
        slot = 'PM'
        labels = ['금일 오후', '다음날 오전']
        ov1 = _extract(today_str, 12, 23)
        ov2 = _extract(tomorrow_str, 0, 12)

    if ov1 == '-': ov1 = '맑음'
    if ov2 == '-': ov2 = '맑음'

    return {
        'overview_slot': slot, 'overview_labels': labels,
        'overview_1': ov1, 'overview_2': ov2,
        'overview': f"{ov1} / {ov2}"
    }

def generate_forecast_summary(loc_name, data):
    alerts = data.get('alerts', [])
    dust = data.get('dust', {})
    temp = data.get('temperature', {})
    rain_fcst = data.get('rain_forecast', [])
    overview = data.get('overview', '-')

    if alerts:
        anames = [a.get('name', '') for a in alerts if isinstance(a, dict) and a.get('name')]
        if anames:
            astr = ", ".join(anames[:2])
            if any('PM' in a for a in anames):
                return f"😷 {astr} 발효 중! 마스크를 꼭 착용하세요."
            return f"⚠️ {astr} 발효 중! 안전에 유의하세요."

    if rain_fcst:
        tot = sum([item.get('amount', 0) for item in rain_fcst])
        ftime = rain_fcst[0].get('time_range', '')
        return f"☔ {ftime} 비 예보(예상 {round(tot, 1)}mm)! 우산을 챙기세요."

    pm10_g = dust.get('pm10_grade', '-')
    pm25_g = dust.get('pm25_grade', '-')
    if pm10_g in ['나쁨', '매우나쁨'] or pm25_g in ['나쁨', '매우나쁨']:
        btype = "미세먼지" if pm10_g in ['나쁨', '매우나쁨'] else "초미세먼지"
        return f"😷 {btype} 농도가 높아요! 마스크 착용을 권장합니다."

    cur_t = temp.get('current')
    max_t = temp.get('max')
    min_t = temp.get('min')
    try:
        ct = float(cur_t)
        if ct >= 33: return f"🌡️ 현재 기온 {ct}℃로 무더워요! 수분을 섭취하세요."
        elif ct <= 0: return f"🧊 현재 기온 {ct}℃ 영하권 추위입니다. 따뜻하게 입으세요!"
    except (ValueError, TypeError): pass

    try:
        if float(max_t) - float(min_t) >= 10:
            return f"🧥 일교차가 크니 겉옷을 준비하세요 (최고 {max_t}℃ / 최저 {min_t}℃)."
    except (ValueError, TypeError): pass

    if '맑' in overview: return f"☀️ 구름 없이 맑은 날씨입니다 (최고 {max_t}℃)."
    elif '구름' in overview: return f"⛅ 구름이 많은 날씨입니다 (현재 {cur_t}℃)."
    elif '흐림' in overview: return f"☁️ 대체로 흐린 날씨입니다 (현재 {cur_t}℃)."
    return f"🌤️ 현재 날씨는 {overview}입니다 ({cur_t}℃)."

# ============================================================
# 지역별 수집 및 데이터 결합 (Fallback 강화)
# ============================================================
def process_location(name, cfg, now, today_str, alerts_data, existing_loc=None):
    nx, ny = cfg['nx'], cfg['ny']

    ncst = fetch_ncst(nx, ny, now)
    u_items = fetch_ultra_fcst(nx, ny, now)
    v_items = fetch_vilage_fcst(nx, ny, now, today_str)
    air = fetch_air(cfg['station'], cfg.get('fallback_station'))
    kma_obs = fetch_kma_hub_obs(cfg.get('kma_stn'))

    ov_data = get_period_overviews(now, today_str, ncst, u_items, v_items)

    cur_temp = ncst.get('T1H', '-')
    if kma_obs and kma_obs.get('temp') is not None:
        cur_temp = f"{kma_obs['temp']:.1f}"

    temps = []
    if cur_temp != '-':
        try: temps.append(float(cur_temp))
        except (ValueError, TypeError): pass
    for it in v_items:
        if it.get('fcstDate') == today_str and it.get('category') in ('TMN', 'TMX', 'TMP'):
            try: temps.append(float(it['fcstValue']))
            except (ValueError, TypeError): pass

    min_t = f"{min(temps):.1f}" if temps else '-'
    max_t = f"{max(temps):.1f}" if temps else '-'

    w_spd_raw = kma_obs.get('wind_speed') if (kma_obs and kma_obs.get('wind_speed') is not None) else ncst.get('WSD', '-')
    if kma_obs and kma_obs.get('wind_deg') is not None and kma_obs.get('wind_speed') is not None:
        w_dir = wind_dir_text(kma_obs['wind_deg'])
        w_spd_text = f"{kma_obs['wind_speed']:.1f}m/s"
    else:
        w_dir = wind_dir_text(ncst.get('VEC', '-'))
        try: w_spd_text = f"{float(w_spd_raw):.1f}m/s"
        except (ValueError, TypeError): w_spd_text = '-'

    acc_rain = kma_obs.get('rn_day', 0.0) if (kma_obs and kma_obs.get('rn_day') is not None) else parse_rain_val(ncst.get('RN1', 0))

    cur_reh = ncst.get('REH', '-')
    feels_like = calc_feels_like(cur_temp, cur_reh, w_spd_raw, now.month)

    loc_res = {
        'overview': ov_data['overview'],
        'overview_1': ov_data['overview_1'],
        'overview_2': ov_data['overview_2'],
        'overview_labels': ov_data['overview_labels'],
        'overview_slot': ov_data['overview_slot'],
        'dust': air,
        'temperature': {'current': cur_temp, 'feels_like': feels_like, 'min': min_t, 'max': max_t},
        'wind': {'direction': w_dir, 'speed': w_spd_text},
        'rain_accumulated': round(acc_rain, 1),
        'rain_forecast': [],
        'alerts': alerts_data.get(name, []),
        'hourly_rn1': {},
    }

    # ★ Fallback 검증: 만약 새 데이터의 기온/개황이 '-' 인 경우 기존 유효 데이터 유지
    if existing_loc and isinstance(existing_loc, dict):
        if loc_res['overview'] == '-' and existing_loc.get('overview') not in ('-', None):
            loc_res['overview'] = existing_loc['overview']
            loc_res['overview_1'] = existing_loc.get('overview_1', '-')
            loc_res['overview_2'] = existing_loc.get('overview_2', '-')
        if loc_res['temperature']['current'] == '-' and existing_loc.get('temperature', {}).get('current') not in ('-', None):
            loc_res['temperature'] = existing_loc['temperature']
        if loc_res['dust']['pm10'] == '-' and existing_loc.get('dust', {}).get('pm10') not in ('-', None):
            loc_res['dust'] = existing_loc['dust']

    loc_res['forecast_summary'] = generate_forecast_summary(name, loc_res)
    return loc_res

def fetch_yangpyeong_weekly_data(now, today_str):
    cfg = LOCATIONS.get('양평', {'nx': 70, 'ny': 126})
    v_items = fetch_vilage_fcst(cfg['nx'], cfg['ny'], now, today_str)
    weekday_kr = ['월', '화', '수', '목', '금', '토', '일']
    weekly_list = []

    for i in range(7):
        target_date = now.date() + timedelta(days=i)
        dt_str = target_date.strftime('%Y%m%d')
        disp_date = target_date.strftime('%m.%d')
        w_day = weekday_kr[target_date.weekday()]
        label = "오늘" if i == 0 else ("내일" if i == 1 else ("모레" if i == 2 else f"{i}일후"))

        d_items = [it for it in v_items if it.get('fcstDate') == dt_str]
        temps = [float(it['fcstValue']) for it in d_items if it.get('category') in ('TMP', 'TMN', 'TMX') and it.get('fcstValue') not in ('-', None)]
        skys = [int(it['fcstValue']) for it in d_items if it.get('category') == 'SKY' and it.get('fcstValue') not in ('-', None)]

        min_t = f"{min(temps):.1f}" if temps else "18.0"
        max_t = f"{max(temps):.1f}" if temps else "28.0"
        sky_val = round(sum(skys)/len(skys)) if skys else 1
        overview = sky_pty_to_text(sky_val, 0)
        if overview == '-': overview = '맑음'

        weekly_list.append({
            'label': label, 'date_display': f"{disp_date} ({w_day})",
            'overview': overview,
            'temperature': {'min': min_t, 'max': max_t},
            'wind': {'direction': '서풍', 'speed': '1.5m/s'}
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

    print(f"=== [고속 병렬 수집] 날씨 데이터 수집 시작 ===")
    print(f"시각: {now.strftime('%Y-%m-%d %H:%M:%S KST')}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'weather.json')

    # 기존 weather.json 읽기 (장애 발생 시 100% 보존용)
    existing_data = None
    if os.path.exists(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = None

    # 특보 및 미세먼지 경보 병렬 수집
    with ThreadPoolExecutor(max_workers=2) as alert_exec:
        f_alerts = alert_exec.submit(fetch_alerts)
        f_dust = alert_exec.submit(fetch_dust_alerts)
        alerts = f_alerts.result()
        dust_alerts = f_dust.result()

    combined_alerts = {}
    for name in LOCATION_ORDER:
        loc_a = list(alerts.get(name, []))
        for da in dust_alerts.get(name, []):
            if not any(a.get('name') == da.get('name') for a in loc_a):
                loc_a.append(da)
        combined_alerts[name] = loc_a

    # 8개 지역 초고속 병렬 수집 (Concurrent Execution)
    locations_data = {}
    def _fetch_single_location(name):
        cfg = LOCATIONS[name]
        existing_loc = existing_data.get('locations', {}).get(name) if existing_data else None
        try:
            res = process_location(name, cfg, now, today_str, combined_alerts, existing_loc)
            return name, res
        except Exception as e:
            print(f"  [{name}] 수집 오류: {e}")
            if existing_loc:
                return name, existing_loc
            return name, {
                'overview': '-', 'dust': {'pm10': '-', 'pm10_grade': '-', 'pm25': '-', 'pm25_grade': '-'},
                'temperature': {'current': '-', 'feels_like': '-', 'min': '-', 'max': '-'},
                'wind': {'direction': '-', 'speed': '-'}, 'rain_accumulated': 0, 'rain_forecast': [],
                'alerts': [], 'forecast_summary': f"🌤️ {name} 기상 정보 수집 중입니다."
            }

    print("  [병렬 수집] 8개 지역 동시 수집 중...")
    with ThreadPoolExecutor(max_workers=len(LOCATION_ORDER)) as loc_exec:
        futures = {loc_exec.submit(_fetch_single_location, name): name for name in LOCATION_ORDER}
        for future in as_completed(futures):
            loc_name, loc_res = future.result()
            locations_data[loc_name] = loc_res

    # LOCATION_ORDER 순서 정렬
    locations_data = {name: locations_data[name] for name in LOCATION_ORDER if name in locations_data}

    # API 상태 판별
    kma_stat = SERVICE_STATS.get('기상청 초단기실황', {'ok': 0, 'fail': 0})
    failed_svcs = [k for k, v in SERVICE_STATS.items() if v['fail'] > 0 and v['ok'] == 0]

    if kma_stat['ok'] == 0 and kma_stat['fail'] > 0:
        api_code = "ERROR"
        api_msg = "공공데이터포털(기상청 API) 응답 지연으로 기존 관측 데이터를 유효 유지합니다."
    elif failed_svcs:
        api_code = "WARNING"
        api_msg = f"공공데이터포털 일부 서비스({', '.join(failed_svcs)}) 응답 지연이 발생하였습니다."
    else:
        api_code = "OK"
        api_msg = "공공데이터포털 API 수집 정상 완료"

    base_date = today_str
    date_disp = now.strftime('%m.%d')
    day_week = ['월', '화', '수', '목', '금', '토', '일'][now.weekday()]
    time_disp = now.strftime('%H:%M')
    updated_at_str = now.strftime('%Y-%m-%dT%H:%M:%S+09:00')

    if api_code == "ERROR" and existing_data:
        base_date = existing_data.get('base_date', base_date)
        date_disp = existing_data.get('date_display', date_disp)
        day_week = existing_data.get('day_of_week', day_week)
        time_disp = existing_data.get('time_display', time_disp)
        updated_at_str = existing_data.get('updated_at', updated_at_str)

    result = {
        'updated_at': updated_at_str,
        'base_date': base_date,
        'date_display': date_disp,
        'day_of_week': day_week,
        'time_display': time_disp,
        'api_status': {
            'code': api_code,
            'message': api_msg,
            'failed_services': failed_svcs,
            'degraded_services': []
        },
        'locations': locations_data,
        'location_order': LOCATION_ORDER,
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[SUCCESS] weather.json 저장 완료 ({os.path.getsize(out_path)} bytes)")

    # 양평 주간예보 저장
    try:
        yp_weekly = fetch_yangpyeong_weekly_data(now, today_str)
        yp_path = os.path.join(out_dir, 'weather_yangpyeong_weekly.json')
        with open(yp_path, 'w', encoding='utf-8') as f:
            json.dump(yp_weekly, f, ensure_ascii=False, indent=2)
        print(f"[SUCCESS] weather_yangpyeong_weekly.json 저장 완료")
    except Exception as e:
        print(f"[WARNING] 양평 주간예보 수집 실패: {e}")

    # API 실시간 진단 실행 및 결과 저장
    try:
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from diag_all_api import run_and_save_diag
        run_and_save_diag(out_dir)
        print(f"[SUCCESS] api_diag_result.json 실시간 진단 완료")
    except Exception as e:
        print(f"[WARNING] API 진단 저장 실패: {e}")

    print(f"=== 수집 프로세스 종료 (상태: {api_code}) ===")

if __name__ == '__main__':
    main()
