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
    '양평': {'nx': 70, 'ny': 126, 'station': '양평읍',   'fallback_station': '가평읍', 'alert_region': '양평'},    # 노도성당
    '경산': {'nx': 92, 'ny': 91,  'station': '시지동',   'fallback_station': '만촌동', 'alert_region': '경산'},    # 제광파종기
    '사천': {'nx': 81, 'ny': 72,  'station': '사천읍',   'fallback_station': '향촌동', 'alert_region': '사천'},    # 후전삼거리
    '함안': {'nx': 87, 'ny': 78,  'station': '가야읍',   'fallback_station': '내서읍', 'alert_region': '함안'},    # 국군복지단 충무마트
    '성주': {'nx': 85, 'ny': 92,  'station': '성주군',   'fallback_station': '다사읍', 'alert_region': '성주'},    # 초전면
    '세종': {'nx': 62, 'ny': 105, 'station': '조치원읍', 'fallback_station': '아름동', 'alert_region': '세종'},    # 세종레스텔
    '계룡': {'nx': 66, 'ny': 100, 'station': '엄사면',   'fallback_station': '논산',   'alert_region': '계룡'},    # 품안마을아파트(신도안면)
    '임실': {'nx': 67, 'ny': 85,  'station': '임실읍',   'fallback_station': '삼천동', 'alert_region': '임실'},    # 충경신병교육대
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
            resp = requests.get(full_url, params=params, timeout=30)
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
    """기상특보"""
    data = api_call(f"{BASE_ALERT}/getWthrWrnMsg", {
        'pageNo': '1', 'numOfRows': '1', 'dataType': 'JSON', 'stnId': '108',
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

    # t6 (현재 특보 발효 현황) + t2 (특보 변경 내역) + other 통합
    full_text = f"{item_data.get('t6', '')}\n{item_data.get('t2', '')}\n{item_data.get('other', '')}"
    full_text = full_text.replace('\r', '')

    alert_types = [
        '폭염중대경보', '폭염경보', '폭염주의보', '호우경보', '호우주의보',
        '열대야주의보', '강풍경보', '강풍주의보', '태풍경보', '태풍주의보',
        '대설경보', '대설주의보', '한파경보', '한파주의보', '건조경보', '건조주의보',
        '황사경보', '황사주의보',
    ]

    result = {}
    for loc_name, cfg in LOCATIONS.items():
        region_kw = cfg['alert_region']
        matched = []
        for line in full_text.split('\n'):
            line_str = line.strip()
            if not line_str:
                continue
            if region_kw in line_str:
                for at in alert_types:
                    if at in line_str and at not in matched:
                        matched.append(at)
        result[loc_name] = matched

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

    # 기온 (현재 T1H, 최저 TMN, 최고 TMX)
    cur_temp = ncst.get('T1H', '-')
    min_temp = '-'
    max_temp = '-'
    for it in v_items:
        if it['category'] == 'TMN':
            min_temp = it['fcstValue']
        elif it['category'] == 'TMX':
            max_temp = it['fcstValue']

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

    return {
        'overview': overview,
        'dust': air,
        'temperature': {'current': cur_temp, 'min': min_temp, 'max': max_temp},
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
                'temperature': {'current': '-', 'min': '-', 'max': '-'},
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
