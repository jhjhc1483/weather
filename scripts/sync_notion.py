#!/usr/bin/env python3
"""
data/weather.json → Notion 데이터베이스 동기화

GitHub Actions에서 fetch_weather.py 실행 직후에 호출된다.
Notion 쪽에 만들어 둔 속성(칼럼)만 골라서 채우므로, 데이터베이스에
원하는 칼럼만 만들어 두면 나머지는 자동으로 무시된다.

필요한 환경변수
  NOTION_TOKEN   : 내부 통합(integration) 토큰 (ntn_... 또는 secret_...)
  NOTION_DB_ID   : 대상 데이터베이스 ID (32자 hex, 하이픈 유무 무관)

선택 환경변수
  NOTION_VERSION : API 버전 (기본 2025-09-03)
  NOTION_ALIASES : 지역명 치환 JSON. 예) {"계룡":"A","성주":"B"}
                   공개 페이지에 실제 지역명을 노출하고 싶지 않을 때 사용.
"""

import json
import os
import sys
import time

import requests

API = "https://api.notion.com/v1"
TIMEOUT = 30
RETRY = 3
PAUSE = 0.35  # Notion 평균 3req/s 제한 대응

TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
DB_ID = os.environ.get("NOTION_DB_ID", "").strip()
VERSION = os.environ.get("NOTION_VERSION", "2025-09-03").strip()

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": VERSION,
    "Content-Type": "application/json",
}

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(HERE, "..", "data", "weather.json")


# ------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------
def call(method, path, payload=None):
    url = f"{API}{path}"
    last = None
    for attempt in range(RETRY):
        try:
            res = requests.request(
                method, url, headers=HEADERS, json=payload, timeout=TIMEOUT
            )
        except requests.RequestException as e:
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
            continue

        if res.status_code == 429:
            wait = float(res.headers.get("Retry-After", 2))
            time.sleep(wait)
            continue

        if res.ok:
            return res.json()

        # 4xx는 재시도해도 동일하므로 즉시 중단
        if 400 <= res.status_code < 500:
            raise RuntimeError(f"{method} {path} → {res.status_code}: {res.text[:400]}")

        last = f"{res.status_code}: {res.text[:200]}"
        time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"{method} {path} 실패 — {last}")


# ------------------------------------------------------------------
# 값 변환
# ------------------------------------------------------------------
def to_num(v):
    """'28.6' → 28.6 / '-' 또는 '' → None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("mm", "").replace("m/s", "").replace("℃", "")
    if s in ("", "-", "N/A", "없음"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def rt(text):
    """rich_text / title 공통 페이로드"""
    text = "" if text is None else str(text)
    if not text:
        return []
    return [{"type": "text", "text": {"content": text[:2000]}}]


def to_property(value, ptype):
    """논리값을 Notion 속성 타입에 맞춰 변환. 지원하지 않는 타입이면 None."""
    if ptype == "title":
        return {"title": rt(value if not isinstance(value, list) else ", ".join(value))}

    if ptype == "rich_text":
        if isinstance(value, list):
            value = ", ".join(str(x) for x in value)
        return {"rich_text": rt(value)}

    if ptype == "number":
        return {"number": to_num(value)}

    if ptype == "select":
        if isinstance(value, list):
            value = value[0] if value else None
        name = str(value).strip() if value not in (None, "") else ""
        return {"select": {"name": name[:100]} if name else None}

    if ptype == "multi_select":
        if isinstance(value, list):
            items = value
        elif value in (None, ""):
            items = []
        else:
            items = [value]
        # Notion multi_select 옵션명에는 쉼표를 쓸 수 없다
        return {
            "multi_select": [
                {"name": str(i).replace(",", " ").strip()[:100]} for i in items if str(i).strip()
            ]
        }

    if ptype == "date":
        return {"date": {"start": value} if value else None}

    if ptype == "checkbox":
        return {"checkbox": bool(value)}

    if ptype == "url":
        return {"url": str(value) if value else None}

    return None  # formula, rollup, status 등은 API로 직접 못 씀


# ------------------------------------------------------------------
# 지역별 논리값 구성
# ------------------------------------------------------------------
def build_values(display_name, loc, meta):
    temp = loc.get("temperature", {}) or {}
    wind = loc.get("wind", {}) or {}
    dust = loc.get("dust", {}) or {}

    # 특보: dict 또는 문자열 모두 허용
    alert_names, alert_detail = [], []
    for a in loc.get("alerts", []) or []:
        if isinstance(a, str):
            alert_names.append(a)
            alert_detail.append(a)
        else:
            nm = a.get("name", "")
            if not nm:
                continue
            alert_names.append(nm)
            st = a.get("status", "발효중")
            eff = a.get("effective_time", "")
            alert_detail.append(f"{nm}({st}{' ' + eff if eff else ''})")

    # 강수 예보 구간
    rf = loc.get("rain_forecast", []) or []
    rain_fc = " / ".join(
        f"{i.get('time_range', '')} {i.get('amount', 0)}mm" for i in rf if isinstance(i, dict)
    )

    tmin, tmax = temp.get("min", ""), temp.get("max", "")
    pm10, pm10g = dust.get("pm10", ""), dust.get("pm10_grade", "")
    pm25, pm25g = dust.get("pm25", ""), dust.get("pm25_grade", "")
    fallback = " *" if dust.get("is_fallback") else ""

    return {
        "지역": display_name,
        "하늘": loc.get("overview", ""),
        "기온": temp.get("current", ""),
        "체감": temp.get("feels_like", ""),
        "최저": tmin,
        "최고": tmax,
        "최저최고": f"{tmin} / {tmax}" if tmin or tmax else "",
        "바람": f"{wind.get('direction', '')} {wind.get('speed', '')}".strip(),
        "강수량": loc.get("rain_accumulated", 0),
        "강수예보": rain_fc or "-",
        "미세먼지": f"{pm10g} {pm10}{fallback}".strip(),
        "초미세먼지": f"{pm25g} {pm25}{fallback}".strip(),
        "특보": alert_names,
        "특보상세": ", ".join(alert_detail) or "-",
        "갱신": meta.get("updated_at", ""),
        "갱신시각": f"{meta.get('date_display', '')}({meta.get('day_of_week', '')}) {meta.get('time_display', '')}".strip(),
    }


def build_properties(values, schema):
    """스키마에 실제로 존재하는 속성만 페이로드로 만든다."""
    props = {}
    for key, prop in schema.items():
        if key not in values:
            continue
        payload = to_property(values[key], prop.get("type"))
        if payload is not None:
            props[key] = payload
    return props


# ------------------------------------------------------------------
# 메인
# ------------------------------------------------------------------
def main():
    if not TOKEN or not DB_ID:
        print("NOTION_TOKEN / NOTION_DB_ID 미설정 — Notion 동기화를 건너뜁니다.")
        return 0

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    locations = data.get("locations", {})
    if not locations:
        print("weather.json에 지역 데이터가 없습니다. 중단합니다.")
        return 1

    order = data.get("location_order") or list(locations.keys())
    aliases = {}
    raw_alias = os.environ.get("NOTION_ALIASES", "").strip()
    if raw_alias:
        try:
            aliases = json.loads(raw_alias)
        except json.JSONDecodeError:
            print("NOTION_ALIASES 파싱 실패 — 원래 지역명을 사용합니다.")

    # 1) 데이터베이스 → 데이터소스 ID
    db = call("GET", f"/databases/{DB_ID}")
    sources = db.get("data_sources") or []
    if not sources:
        raise RuntimeError(
            "데이터소스를 찾지 못했습니다. 통합에 데이터베이스가 연결됐는지 확인하세요."
        )
    if len(sources) > 1:
        names = ", ".join(s.get("name", "?") for s in sources)
        print(f"데이터소스가 여러 개입니다({names}). 첫 번째를 사용합니다.")
    ds_id = sources[0]["id"]

    # 2) 스키마 조회
    ds = call("GET", f"/data_sources/{ds_id}")
    schema = ds.get("properties", {})
    title_key = next(
        (k for k, v in schema.items() if v.get("type") == "title"), None
    )
    if not title_key:
        raise RuntimeError("제목(title) 속성이 없습니다.")
    print(f"속성: {', '.join(schema.keys())}")

    # 3) 기존 행 수집 (제목 → page_id)
    existing, cursor = {}, None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        res = call("POST", f"/data_sources/{ds_id}/query", payload)
        for page in res.get("results", []):
            tp = page.get("properties", {}).get(title_key, {}).get("title", [])
            key = "".join(t.get("plain_text", "") for t in tp).strip()
            if key:
                existing[key] = page["id"]
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
        time.sleep(PAUSE)

    # 4) 지역별 upsert
    created = updated = 0
    for name in order:
        loc = locations.get(name)
        if not loc:
            continue

        display = aliases.get(name, name)
        values = build_values(display, loc, data)
        # 제목 속성명이 '지역'이 아닐 수도 있으므로 맞춰준다
        values[title_key] = display
        props = build_properties(values, schema)

        if display in existing:
            call("PATCH", f"/pages/{existing[display]}", {"properties": props})
            updated += 1
        else:
            call(
                "POST",
                "/pages",
                {
                    "parent": {"type": "data_source_id", "data_source_id": ds_id},
                    "properties": props,
                },
            )
            created += 1
        time.sleep(PAUSE)

    print(f"Notion 동기화 완료 — 갱신 {updated}건, 생성 {created}건 ({data.get('updated_at', '')})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Notion 동기화 실패: {e}", file=sys.stderr)
        sys.exit(1)
