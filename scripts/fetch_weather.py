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

API_KEY = os.environ.get('DATA_GO_KR_KEY', '')
if not API_KEY:
    print("ERROR: DATA_GO_KR_KEY 환경변수가 설정되지 않았습니다.")
    sys.exit(1)

# ============================================================
# 8개 지역 설정
# nx, ny: 기상청 격자좌표
# station: 에어코리아 실제 측정소명 (검증 완료)
# alert_region: 기상특보 검색 키워드
# ============================================================
LOCATIONS = {
    '양평': {'nx': 70, 'ny': 126, 'station': '양평읍',   'fallback_station': '가평읍', 'alert_region': '양평', 'province': '경기도'},    # 노도성당
    '경산': {'nx': 92, 'ny': 91,  'station': '시지동',   'fallback_station': '만촌동', 'alert_region': '경산', 'province': '경상북도'},    # 제광파종기
    '사천': {'nx': 81, 'ny': 72,  'station': '사천읍',   'fallback_station': '향촌동', 'alert_region': '사천', 'province': '경상남도'},    # 후전삼거리
    '함안': {'nx': 87, 'ny': 78,  'station': '가야읍',   'fallback_station': '내서읍', 'alert_region': '함안', 'province': '경상남도'},    # 국군복지단 충무마트
    '성주': {'nx': 85, 'ny': 92,  'station': '성주군',   'fallback_station': '다사읍', 'alert_region': '성주', 'province': '경상북도'},    # 초전면
    '세종': {'nx': 65, 'ny': 104, 'station': '아름동',   'fallback_station': '조치원읍', 'alert_region': '세종', 'province': '세종'},      # 세종레스텔(연서면 봉암리)
    '계룡': {'nx': 66, 'ny': 100, 'station': '엄사면',   'fallback_station': '논산',   'alert_region': '계룡', 'province': '충청남도'},    # 품안마을아파트(신도안면)
    '임실': {'nx': 67, 'ny': 85,  'station': '임실읍',   'fallback_station': '삼천동', 'alert_region': '임실', 'province': '전북'},      # 충경신병교육대
}

LOCATION_ORDER = ['양평', '경산', '사천', '함안', '성주', '세종', '계룡', '임실']
KST = timezone(timedelta(hours=9))

BASE_WEATHER = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
BASE_AIR = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
BASE_ALERT = "http://apis.data.go.kr/1360000/WthrWrnInfoService"


# ============================================================
# API 호출 헬퍼
# ============================================================
def api_call(url, params, retries=3):
    encoded_key = quote(API_KEY, safe='')
    full_url = f"{url}?serviceKey={encoded_key}"

    for attempt in range(retries):
        try:
            resp = requests.get(full_url, params=params, timeout=10)
            resp.raise_for_status()

            try:
                data = resp.json()
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                return None

            rc = data.get('response', {}).get('header', {}).get('resultCode', '')
            if rc != '00':
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                return None

            return data
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(1.5)
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
    })
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
    })
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
    })
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
        })
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
        })
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
    """기상특보 (발효 중 + 예정 특보 포함, 발효시각 첨부)"""
    import re

    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')
    yesterday_str = (now - timedelta(days=2)).strftime('%Y%m%d')

    data = api_call(f"{BASE_ALERT}/getWthrWrnMsg", {
        'pageNo': '1', 'numOfRows': '5', 'dataType': 'JSON', 'stnId': '108',
        'fromTmFc': yesterday_str, 'toTmFc': today_str,
    })
    if not data:
        return {}

    try:
        items = data['response']['body']['items']['item']
        if isinstance(items, dict):
            items = [items]
        if not items:
            return {}
        item_data = items[0]
    except (KeyError, TypeError):
        return {}

    alert_types = [
        '폭염중대경보', '폭염경보', '폭염주의보', '호우경보', '호우주의보',
        '열대야주의보', '강풍경보', '강풍주의보', '태풍경보', '태풍주의보',
        '대설경보', '대설주의보', '한파경보', '한파주의보', '건조경보', '건조주의보',
        '황사경보', '황사주의보',
    ]

    now = datetime.now(KST)

    # ── 헬퍼: 특보 지역 텍스트 파싱 ──
    def parse_region_chunks(text):
        chunks = []
        current = ""
        in_paren = False
        for char in text:
            if char == '(':
                in_paren = True
                current += char
            elif char == ')':
                in_paren = False
                current += char
            elif char == ',' and not in_paren:
                if current.strip():
                    chunks.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            chunks.append(current.strip())
        return chunks

    def match_location_to_chunk(chunk, loc_name, cfg):
        region_kw = cfg['alert_region']
        province = cfg.get('province', '')

        # 1. 괄호가 포함된 경우 (예: "충청남도(아산, 부여, 청양 제외)" 또는 "전북자치도(고창, 김제, 임실)")
        m = re.match(r'^([^(]+)\(([^)]+)\)$', chunk)
        if m:
            prov_part = m.group(1).strip()
            inside_part = m.group(2).strip()

            prov_match = (prov_part in province or province in prov_part or 
                          (province == '전북' and ('전북' in prov_part or '전라북도' in prov_part)))

            if not prov_match:
                return False

            if '제외' in inside_part:
                ex_index = inside_part.find('제외')
                excluded_text = inside_part[:ex_index]
                if region_kw in excluded_text:
                    return False  # 제외 목록에 명시됨 -> 매칭 안 됨!
                else:
                    return True   # 도 전체 대상이며 제외 목록에 안 들었으므로 매칭됨!
            else:
                if region_kw in inside_part:
                    return True
                else:
                    return False

        # 2. 괄호가 없는 경우 (예: "세종", "대전", "인천")
        chunk_clean = chunk.strip()
        if region_kw in chunk_clean or (province and (chunk_clean == province or province == chunk_clean)):
            return True

        return False

    # ── 1. t6: 현재 발효 중인 특보 파싱 ──
    t6_text = (item_data.get('t6', '') or '').replace('\r', '')
    t6_lines = [ln.strip() for ln in t6_text.split('\n') if ln.strip()]

    # {지역명: set(특보종류)} — 발효 중
    active_alerts = {}
    for loc_name in LOCATIONS:
        active_alerts[loc_name] = set()

    for line in t6_lines:
        if not line.startswith('o '):
            continue
        matched_type = None
        for at in alert_types:
            if line.startswith(f'o {at}'):
                matched_type = at
                break
        if not matched_type:
            continue

        colon_idx = line.find(':')
        region_text = line[colon_idx + 1:] if colon_idx >= 0 else line
        chunks = parse_region_chunks(region_text)

        for loc_name, cfg in LOCATIONS.items():
            for chunk in chunks:
                if match_location_to_chunk(chunk, loc_name, cfg):
                    active_alerts[loc_name].add(matched_type)
                    break

    # ── 2. t2+t3: 예정 특보 및 오늘/최근 24시간 이내 발표/변경 특보 (is_new) 파싱 ──
    # t2 형식: "(1) 열대야주의보 발표 : 경기도(안산, ...)\n(2) 폭염주의보 발표 : ..."
    # t3 형식: "(1) 열대야주의보 발표 : 2026년 07월 28일 18시 00분\n(2) 폭염주의보 발표 : ..."
    t2_text = (item_data.get('t2', '') or '').replace('\r', '')
    t3_text = (item_data.get('t3', '') or '').replace('\r', '')

    # 새로 발표/변경/강화된 특보 트래킹: {(loc_name, alert_type): True}
    newly_updated_alerts = set()

    # 번호별로 파싱: { "(1)": { action_text, regions_text } }
    t2_entries = {}
    for line in t2_text.split('\n'):
        line = line.strip()
        m = re.match(r'\((\d+)\)\s*(.+)', line)
        if m:
            idx_key = m.group(1)
            rest = m.group(2).strip()
            t2_entries[idx_key] = rest

    t3_entries = {}
    for line in t3_text.split('\n'):
        line = line.strip()
        m = re.match(r'\((\d+)\)\s*(.+)', line)
        if m:
            idx_key = m.group(1)
            rest = m.group(2).strip()
            t3_entries[idx_key] = rest

    # 발효시각 파싱 헬퍼: "폭염주의보 발표 : 2026년 07월 28일 18시 00분" → datetime
    def parse_effective_time(t3_val):
        """t3 값에서 발효시각 추출"""
        m = re.search(r'(\d{4})년\s*(\d{2})월\s*(\d{2})일\s*(\d{1,2})시\s*(\d{2})분', t3_val)
        if m:
            try:
                return datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    int(m.group(4)), int(m.group(5)),
                    tzinfo=KST
                )
            except (ValueError, TypeError):
                pass
        return None

    def format_effective_time(dt_obj):
        """datetime → 표시용 문자열 (MM.DD HH:MM)"""
        if not dt_obj:
            return ''
        return dt_obj.strftime('%m.%d %H:%M')

    # 통보문 발표시각(tmFc) 추출 (YYYYMMDDhhmm)
    tm_fc_str = str(item_data.get('tmFc', ''))
    is_today_report = tm_fc_str.startswith(today_str)

    # t2_entries에서 발표/변경 건 분석
    for idx_key, t2_val in t2_entries.items():
        matched_type = None
        for at in alert_types:
            if at in t2_val:
                matched_type = at
                break
        if not matched_type:
            continue

        colon_idx = t2_val.find(':')
        region_text = t2_val[colon_idx + 1:] if colon_idx >= 0 else t2_val
        chunks = parse_region_chunks(region_text)

        if '발표' in t2_val or '변경' in t2_val:
            for loc_name, cfg in LOCATIONS.items():
                for chunk in chunks:
                    if match_location_to_chunk(chunk, loc_name, cfg):
                        newly_updated_alerts.add((loc_name, matched_type))
                        break

    # {지역명: [{name, status, effective_time, is_new}]} — 예정 특보
    scheduled_alerts = {}
    for loc_name in LOCATIONS:
        scheduled_alerts[loc_name] = []

    for idx_key, t2_val in t2_entries.items():
        if '발표' not in t2_val:
            continue

        matched_type = None
        for at in alert_types:
            if at in t2_val:
                matched_type = at
                break
        if not matched_type:
            continue

        t3_val = t3_entries.get(idx_key, '')
        eff_dt = parse_effective_time(t3_val)

        if not eff_dt or eff_dt <= now:
            continue

        colon_idx = t2_val.find(':')
        region_text = t2_val[colon_idx + 1:] if colon_idx >= 0 else t2_val
        chunks = parse_region_chunks(region_text)

        eff_str = format_effective_time(eff_dt)
        is_new = is_today_report or ((loc_name, matched_type) in newly_updated_alerts)

        for loc_name, cfg in LOCATIONS.items():
            for chunk in chunks:
                if match_location_to_chunk(chunk, loc_name, cfg):
                    if matched_type not in active_alerts[loc_name]:
                        scheduled_alerts[loc_name].append({
                            'name': matched_type,
                            'status': '예정',
                            'effective_time': eff_str,
                            'is_new': is_new,
                        })
                    break

    # ── 3. 결과 조합: 발효중 + 예정 ──
    result = {}
    for loc_name in LOCATIONS:
        loc_alerts = []

        for at in alert_types:
            if at in active_alerts[loc_name]:
                is_new = is_today_report or ((loc_name, at) in newly_updated_alerts)
                loc_alerts.append({
                    'name': at,
                    'status': '발효중',
                    'effective_time': '',
                    'is_new': is_new,
                })

        seen_scheduled = set()
        for sa in scheduled_alerts[loc_name]:
            key = sa['name']
            if key not in seen_scheduled:
                seen_scheduled.add(key)
                loc_alerts.append(sa)

        result[loc_name] = loc_alerts

    return result


# ============================================================
# 지역별 데이터 처리 및 정밀 융합
# ============================================================
def process_location(name, cfg, now, today_str, alerts_data):
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

    # 일일 누적 강수량 (00시 ~ 현재 시각까지)
    acc_rain = 0.0
    # 초단기실황 RN1 또는 단기예보 이전시간 누적
    curr_rn1 = parse_rain_val(ncst.get('RN1', 0))
    acc_rain += curr_rn1

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

    return {
        'overview': overview,
        'dust': air,
        'temperature': {'current': cur_temp, 'feels_like': feels_like, 'min': min_temp, 'max': max_temp},
        'wind': {'direction': w_dir, 'speed': w_spd_text},
        'rain_accumulated': round(acc_rain, 1),
        'rain_forecast': rain_forecast,
        'alerts': alerts_data.get(name, []),
    }



# ============================================================
# 메인 실행
# ============================================================
def main():
    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')

    print(f"=== 고도화 날씨 데이터 수집 ===")
    print(f"시각: {now.strftime('%Y-%m-%d %H:%M:%S KST')}")

    alerts = fetch_alerts()
    time.sleep(0.3)

    locations_data = {}
    for name in LOCATION_ORDER:
        cfg = LOCATIONS[name]
        try:
            locations_data[name] = process_location(name, cfg, now, today_str, alerts)
        except Exception as e:
            print(f"  [{name}] 오류: {e}")
            traceback.print_exc()
            locations_data[name] = {
                'overview': '-',
                'dust': {'pm10': '-', 'pm10_grade': '-', 'pm25': '-', 'pm25_grade': '-'},
                'temperature': {'current': '-', 'feels_like': '-', 'min': '-', 'max': '-'},
                'wind': {'direction': '-', 'speed': '-'},
                'rain_accumulated': 0,
                'rain_forecast': [],
                'alerts': [],
            }

    result = {
        'updated_at': now.strftime('%Y-%m-%dT%H:%M:%S+09:00'),
        'base_date': today_str,
        'date_display': now.strftime('%m.%d'),
        'day_of_week': ['월', '화', '수', '목', '금', '토', '일'][now.weekday()],
        'time_display': now.strftime('%H:%M'),
        'locations': locations_data,
        'location_order': LOCATION_ORDER,
    }

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'weather.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n=== 성공적으로 완료: {out_path} ===")


if __name__ == '__main__':
    main()
