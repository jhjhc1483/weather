#!/usr/bin/env python3
"""기상특보 API(getWthrWrnMsg) 단독 진단 스크립트.

'기상청이 죽은 것'과 '우리 쪽 타임아웃/키/파라미터 문제'를 구분하기 위한 도구.
관할 관서별로 실제 응답 시간과 원문 헤더를 그대로 찍는다.

사용:
    python scripts/diag_alert_api.py
    python scripts/diag_alert_api.py --timeout 30 --rows 12 --days 2

판독법:
  - 응답시간이 5초를 넘는 관서가 있다  -> 기존 timeout=5 가 원인. 기상청 정상.
  - resultCode=03                       -> 해당 기간 통보문 없음. 장애 아님.
  - resultCode=30/31/22                 -> 서비스키 미등록/기한만료/트래픽 초과. 우리 쪽 문제.
  - JSON 아님(XML 본문)                 -> 포털 서비스 레벨 오류. 본문 문구 확인.
  - 전 관서 타임아웃 + 응답시간 == 한계 -> 기상청/포털 지연 의심.
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
BASE_ALERT = "https://apis.data.go.kr/1360000/WthrWrnInfoService"

# 108 전국 / 109 서울·인천·경기 / 133 대전·세종·충남 / 143 대구·경북
# 146 전북 / 159 부산·울산·경남
STATIONS = [
    ('108', '전국'), ('109', '서울·인천·경기'), ('133', '대전·세종·충남'),
    ('143', '대구·경북'), ('146', '전북'), ('159', '부산·울산·경남'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=float, default=30.0)
    ap.add_argument('--rows', type=int, default=12)
    ap.add_argument('--days', type=int, default=2)
    args = ap.parse_args()

    key = os.environ.get('DATA_GO_KR_KEY', '')
    if not key:
        print("ERROR: DATA_GO_KR_KEY 환경변수가 없습니다.")
        sys.exit(1)

    now = datetime.now(KST)
    to_tm = now.strftime('%Y%m%d')
    from_tm = (now - timedelta(days=args.days)).strftime('%Y%m%d')

    print(f"=== 기상특보 API 진단 ===")
    print(f"시각      : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f"조회기간  : {from_tm} ~ {to_tm}")
    print(f"numOfRows : {args.rows}   timeout: {args.timeout}s")
    print(f"서비스키  : 길이 {len(key)}, 끝 4자리 ...{key[-4:]}"
          f"{'  (이미 URL 인코딩된 키로 보임: %2B/%3D 포함)' if ('%' in key) else ''}")
    print("-" * 68)

    url = f"{BASE_ALERT}/getWthrWrnMsg?serviceKey={quote(key, safe='')}"
    slow = []

    for stn, name in STATIONS:
        started = time.monotonic()
        try:
            resp = requests.get(url, params={
                'pageNo': '1', 'numOfRows': str(args.rows), 'dataType': 'JSON',
                'stnId': stn, 'fromTmFc': from_tm, 'toTmFc': to_tm,
            }, timeout=args.timeout)
            elapsed = time.monotonic() - started
        except requests.Timeout:
            elapsed = time.monotonic() - started
            print(f"[{stn}] {name:<14} {elapsed:6.2f}s  ✗ 타임아웃")
            slow.append((stn, elapsed))
            continue
        except requests.RequestException as e:
            elapsed = time.monotonic() - started
            print(f"[{stn}] {name:<14} {elapsed:6.2f}s  ✗ {type(e).__name__}: {str(e)[:80]}")
            continue

        size = len(resp.content)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            body = ' '.join(resp.text[:180].split())
            print(f"[{stn}] {name:<14} {elapsed:6.2f}s  ✗ JSON 아님 "
                  f"(HTTP {resp.status_code}, {size}B)\n"
                  f"       본문: {body}")
            continue

        header = (data.get('response', {}) or {}).get('header', {}) or {}
        rc = str(header.get('resultCode', '?'))
        rmsg = str(header.get('resultMsg', ''))

        cnt = 0
        try:
            items = data['response']['body']['items']['item']
            cnt = 1 if isinstance(items, dict) else len(items)
        except (KeyError, TypeError):
            pass

        mark = '✓' if rc == '00' else ('·' if rc == '03' else '✗')
        print(f"[{stn}] {name:<14} {elapsed:6.2f}s  {mark} rc={rc} "
              f"({rmsg}) 통보문 {cnt}건, {size:,}B")
        if elapsed > 5:
            slow.append((stn, elapsed))

    print("-" * 68)
    if slow:
        worst = max(e for _, e in slow)
        print(f"판정: {len(slow)}개 관서가 5초를 초과했습니다 (최대 {worst:.1f}s).")
        print("      → 기존 timeout=5 로는 정상 응답도 실패 처리됩니다. 타임아웃 상향이 정답.")
    else:
        print("판정: 모든 관서가 5초 내 응답. 실패 원인은 타임아웃이 아닙니다.")
        print("      → 위 resultCode / 본문 문구를 확인하세요.")


if __name__ == '__main__':
    main()
