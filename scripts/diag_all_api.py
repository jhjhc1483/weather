#!/usr/bin/env python3
"""대한민국 기상 & 대기질 수집 API 전체 종합 진단 스크립트.

프로젝트에서 사용하는 7개 주요 공공데이터 & 기상청 API 전체의
응답 속도, 결과 코드, JSON/텍스트 정상 반환 유무, 장애 발생 여부를 종합적으로 진단합니다.

점검 대상 7대 서비스:
  1. 기상청 기상특보 API (getWthrWrnMsg)
  2. 기상청 초단기실황 API (getUltraSrtNcst)
  3. 기상청 초단기예보 API (getUltraSrtFcst)
  4. 기상청 단기예보 API (getVilageFcst)
  5. 에어코리아 실시간 미세먼지 API (getMsrstnAcctoRltmMesureDnsty)
  6. 에어코리아 미세먼지 주의보/경보 API (getUlfptcaAlarmInfo)
  7. 기상청 API 허브 실시간 강수관측 API (nph-aws2_min)

사용법:
    python scripts/diag_all_api.py
    python scripts/diag_all_api.py --timeout 20
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

KST = timezone(timedelta(hours=9))

BASE_WEATHER = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
BASE_ALERT = "https://apis.data.go.kr/1360000/WthrWrnInfoService"
BASE_AIR = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
BASE_DUST_ALARM = "https://apis.data.go.kr/B552584/UlfptcaAlarmInqireSvc"
BASE_KMA_HUB = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url"

# 주요 테스트 대상
TEST_LOCATIONS = [
    {'name': '양평', 'nx': 65, 'ny': 123, 'station': '양평읍', 'stn': '212'},
    {'name': '세종', 'nx': 66, 'ny': 103, 'station': '아름동', 'stn': '637'},
    {'name': '계룡', 'nx': 65, 'ny': 99, 'station': '엄사면', 'stn': '640'},
]

STATIONS = [
    ('108', '전국'),
    ('109', '서울·인천·경기'),
    ('133', '대전·세종·충남'),
]


def test_api_call(url, params, key, timeout=20.0):
    encoded_key = quote(key, safe='')
    full_url = f"{url}?serviceKey={encoded_key}"
    started = time.monotonic()
    
    try:
        resp = requests.get(full_url, params=params, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        elapsed = time.monotonic() - started
    except requests.Timeout:
        elapsed = time.monotonic() - started
        return False, elapsed, "타임아웃 초과", 0
    except requests.RequestException as e:
        elapsed = time.monotonic() - started
        return False, elapsed, f"네트워크 오류: {type(e).__name__}", 0

    if resp.status_code >= 400:
        return False, elapsed, f"HTTP {resp.status_code} 오류", len(resp.content)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        snippet = ' '.join(resp.text[:150].split())
        return False, elapsed, f"JSON 파싱 실패 (응답앞부분={snippet})", len(resp.content)

    # 기상청 헤더 구조
    header = (data.get('response', {}) or {}).get('header', {}) or {}
    rc = str(header.get('resultCode', ''))
    rmsg = str(header.get('resultMsg', ''))

    # 에어코리아 헤더 구조
    if not rc:
        body = data.get('response', {}) or {}.get('body', {})
        if isinstance(body, dict) and 'items' in body:
            rc = '00'
            rmsg = 'NORMAL_SERVICE'

    if rc in ('00', '03', '0'):
        return True, elapsed, f"rc={rc} ({rmsg})", len(resp.content)
    
    return False, elapsed, f"API 오류 rc={rc} ({rmsg})", len(resp.content)


def test_kma_hub_call(stn, auth_key, timeout=20.0):
    """기상청 API 허브 관측 API 테스트"""
    if not auth_key:
        return False, 0.0, "KMA_API_HUB_KEY 미설정", 0

    url = f"{BASE_KMA_HUB}/nph-aws2_min"
    params = {'stn': str(stn), 'disp': '1', 'help': '0', 'authKey': auth_key}
    started = time.monotonic()
    try:
        resp = requests.get(url, params=params, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        elapsed = time.monotonic() - started
        if resp.status_code == 200:
            lines = [l.strip() for l in resp.text.splitlines() if l.strip() and not l.startswith('#')]
            if lines and ',' in lines[-1]:
                return True, elapsed, "정상 관측 수집 (rc=00)", len(resp.content)
            return False, elapsed, "응답 형식 미흡", len(resp.content)
        return False, elapsed, f"HTTP {resp.status_code} 오류", len(resp.content)
    except Exception as e:
        elapsed = time.monotonic() - started
        return False, elapsed, f"오류: {type(e).__name__}", 0


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=float, default=20.0, help="호출 타임아웃(초)")
    args = ap.parse_args()

    key = os.environ.get('DATA_GO_KR_KEY', '')
    kma_key = os.environ.get('KMA_API_HUB_KEY', '')
    if not key:
        print("ERROR: DATA_GO_KR_KEY 환경변수가 설정되어 있지 않습니다.")
        sys.exit(1)

    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')

    print("=" * 72)
    print(" [종합 API 진단] 대한민국 기상 & 대기질 수집 API 전체 상태 점검")
    print(f" 진단 시각 : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f" 타임아웃 : {args.timeout}초")
    print(f" 데이터포털키 : 길이 {len(key)}자 (...{key[-4:]})")
    print(f" 기상청허브키 : {'확인됨 (' + kma_key[:5] + '...)' if kma_key else '미설정'}")
    print("=" * 72)

    results = []

    # 1. 기상특보 API
    print("\n[1/7] 기상청 기상특보 API (getWthrWrnMsg)")
    for stn, name in STATIONS:
        ok, elapsed, msg, size = test_api_call(
            f"{BASE_ALERT}/getWthrWrnMsg",
            {'pageNo': '1', 'numOfRows': '5', 'dataType': 'JSON', 'stnId': stn, 'fromTmFc': today_str, 'toTmFc': today_str},
            key, timeout=args.timeout
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{stn}] {name:<14} {elapsed:6.2f}s | {msg} ({size:,}B)")
        results.append(('기상특보', name, ok, elapsed, msg))

    # 2. 초단기실황 API
    print("\n[2/7] 기상청 초단기실황 API (getUltraSrtNcst)")
    check_time = (now - timedelta(minutes=40))
    bd = check_time.strftime('%Y%m%d')
    bt = check_time.strftime('%H') + '00'
    for loc in TEST_LOCATIONS:
        ok, elapsed, msg, size = test_api_call(
            f"{BASE_WEATHER}/getUltraSrtNcst",
            {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': bd, 'base_time': bt, 'nx': str(loc['nx']), 'ny': str(loc['ny'])},
            key, timeout=args.timeout
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{loc['name']}] 격자({loc['nx']},{loc['ny']})   {elapsed:6.2f}s | {msg} ({size:,}B)")
        results.append(('초단기실황', loc['name'], ok, elapsed, msg))

    # 3. 초단기예보 API
    print("\n[3/7] 기상청 초단기예보 API (getUltraSrtFcst)")
    check_time_u = (now - timedelta(minutes=45))
    bd_u = check_time_u.strftime('%Y%m%d')
    bt_u = check_time_u.strftime('%H') + '00'
    for loc in TEST_LOCATIONS:
        ok, elapsed, msg, size = test_api_call(
            f"{BASE_WEATHER}/getUltraSrtFcst",
            {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': bd_u, 'base_time': bt_u, 'nx': str(loc['nx']), 'ny': str(loc['ny'])},
            key, timeout=args.timeout
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{loc['name']}] 격자({loc['nx']},{loc['ny']})   {elapsed:6.2f}s | {msg} ({size:,}B)")
        results.append(('초단기예보', loc['name'], ok, elapsed, msg))

    # 4. 단기예보 API
    print("\n[4/7] 기상청 단기예보 API (getVilageFcst)")
    for loc in TEST_LOCATIONS:
        ok, elapsed, msg, size = test_api_call(
            f"{BASE_WEATHER}/getVilageFcst",
            {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': today_str, 'base_time': '0200', 'nx': str(loc['nx']), 'ny': str(loc['ny'])},
            key, timeout=args.timeout
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{loc['name']}] 격자({loc['nx']},{loc['ny']})   {elapsed:6.2f}s | {msg} ({size:,}B)")
        results.append(('단기예보', loc['name'], ok, elapsed, msg))

    # 5. 에어코리아 미세먼지 API
    print("\n[5/7] 에어코리아 미세먼지 API (getMsrstnAcctoRltmMesureDnsty)")
    for loc in TEST_LOCATIONS:
        ok, elapsed, msg, size = test_api_call(
            f"{BASE_AIR}/getMsrstnAcctoRltmMesureDnsty",
            {'returnType': 'json', 'stationName': loc['station'], 'dataTerm': 'DAILY', 'ver': '1.3', 'numOfRows': '1'},
            key, timeout=args.timeout
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{loc['name']}] 측정소({loc['station']}) {elapsed:6.2f}s | {msg} ({size:,}B)")
        results.append(('에어코리아', loc['name'], ok, elapsed, msg))

    # 6. 에어코리아 미세먼지 주의보/경보 API
    print("\n[6/7] 에어코리아 미세먼지 주의보/경보 API (getUlfptcaAlarmInfo)")
    ok, elapsed, msg, size = test_api_call(
        f"{BASE_DUST_ALARM}/getUlfptcaAlarmInfo",
        {'returnType': 'json', 'year': str(now.year), 'numOfRows': '5', 'pageNo': '1'},
        key, timeout=args.timeout
    )
    mark = "✓" if ok else "✗"
    print(f"  {mark} [전국] 발효현황조회   {elapsed:6.2f}s | {msg} ({size:,}B)")
    results.append(('미세먼지경보', '전국', ok, elapsed, msg))

    # 7. 기상청 API 허브 실시간 강수관측 API
    print("\n[7/7] 기상청 API 허브 실시간 강수관측 API (nph-aws2_min)")
    for loc in TEST_LOCATIONS:
        ok, elapsed, msg, size = test_kma_hub_call(
            loc['stn'], kma_key, timeout=args.timeout
        )
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{loc['name']}] 관측소({loc['stn']})   {elapsed:6.2f}s | {msg} ({size:,}B)")
        results.append(('API허브강수', loc['name'], ok, elapsed, msg))

    # 종합 리포트
    print("\n" + "=" * 72)
    print(" 📊 종합 진단 결과 리포트")
    print("=" * 72)
    
    fails = [r for r in results if not r[2]]
    slows = [r for r in results if r[3] > 5.0]

    if not fails and not slows:
        print(" ✅ [ALL OK] 모든 API 서비스가 5초 이내에 정상 응답을 반환했습니다!")
    else:
        if fails:
            print(f" ⚠️ [경고] {len(fails)}개 요청에서 장애/오류가 발생했습니다:")
            for sname, lname, _, el, m in fails:
                print(f"    - [{sname}] {lname}: {m} ({el:.2f}s)")
        if slows:
            print(f" 🐢 [지연] {len(slows)}개 요청이 5초 이상 소요되었습니다:")
            for sname, lname, _, el, _ in slows:
                print(f"    - [{sname}] {lname}: {el:.2f}초 소요")
    
    print("=" * 72)

    # data/api_diag_result.json 자동 최신화 저장
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    run_and_save_diag(out_dir)
    print(f"\n  [진단 결과 저장 완료] {os.path.join(out_dir, 'api_diag_result.json')}")


def run_and_save_diag(out_dir=None):
    """7개 API 전체 진단을 수행하고 결과를 JSON 객체로 반환 및 data/api_diag_result.json 에 저장"""
    key = os.environ.get('DATA_GO_KR_KEY', '').strip()
    kma_key = os.environ.get('KMA_API_HUB_KEY', '').strip()
    if not key:
        return {'all_ok': False, 'message': 'DATA_GO_KR_KEY 환경변수가 설정되어 있지 않습니다.', 'results': []}

    now = datetime.now(KST)
    today_str = now.strftime('%Y%m%d')
    check_time = (now - timedelta(minutes=45))
    bd = check_time.strftime('%Y%m%d')
    bt = check_time.strftime('%H') + '00'

    services = [
        {'name': '기상청 기상특보 API', 'type': 'portal', 'url': f"{BASE_ALERT}/getWthrWrnMsg", 'params': {'pageNo': '1', 'numOfRows': '5', 'dataType': 'JSON', 'stnId': '108', 'fromTmFc': today_str, 'toTmFc': today_str}},
        {'name': '기상청 초단기실황 API', 'type': 'portal', 'url': f"{BASE_WEATHER}/getUltraSrtNcst", 'params': {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': bd, 'base_time': bt, 'nx': '65', 'ny': '123'}},
        {'name': '기상청 초단기예보 API', 'type': 'portal', 'url': f"{BASE_WEATHER}/getUltraSrtFcst", 'params': {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': bd, 'base_time': bt, 'nx': '65', 'ny': '123'}},
        {'name': '기상청 단기예보 API', 'type': 'portal', 'url': f"{BASE_WEATHER}/getVilageFcst", 'params': {'pageNo': '1', 'numOfRows': '10', 'dataType': 'JSON', 'base_date': today_str, 'base_time': '0200', 'nx': '65', 'ny': '123'}},
        {'name': '에어코리아 미세먼지 API', 'type': 'portal', 'url': f"{BASE_AIR}/getMsrstnAcctoRltmMesureDnsty", 'params': {'returnType': 'json', 'stationName': '양평읍', 'dataTerm': 'DAILY', 'ver': '1.3', 'numOfRows': '1'}},
        {'name': '에어코리아 미세먼지 경보 API', 'type': 'portal', 'url': f"{BASE_DUST_ALARM}/getUlfptcaAlarmInfo", 'params': {'returnType': 'json', 'year': str(now.year), 'numOfRows': '5', 'pageNo': '1'}},
        {'name': '기상청 API 허브 강수관측', 'type': 'kma_hub', 'stn': '212'},
    ]

    diag_results = []
    all_ok = True

    for s in services:
        if s['type'] == 'kma_hub':
            ok, elapsed, msg, _ = test_kma_hub_call(s['stn'], kma_key, timeout=8.0)
        else:
            ok, elapsed, msg, _ = test_api_call(s['url'], s['params'], key, timeout=8.0)

        if not ok:
            all_ok = False
        diag_results.append({
            'name': s['name'],
            'ok': ok,
            'elapsed': elapsed,
            'message': msg
        })

    res = {
        'updated_at': now.strftime('%Y-%m-%dT%H:%M:%S+09:00'),
        'timestamp': now.strftime('%Y-%m-%d %H:%M:%S KST'),
        'all_ok': all_ok,
        'results': diag_results
    }

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        filepath = os.path.join(out_dir, 'api_diag_result.json')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    return res


if __name__ == '__main__':
    main()
