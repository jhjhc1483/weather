#!/usr/bin/env python3
"""
data/weather.json → Notion 표 블록 동기화

원본 대시보드(index.html)와 동일한 배치로 렌더링한다.
  · 세로축(행) = 구분 8개 항목
  · 가로축(열) = 8개 지역
  · 첫 행/첫 열이 헤더

Notion 데이터베이스가 아니라 **일반 페이지 안의 표 블록**을 쓴다.
데이터베이스는 행이 곧 레코드라서 원본처럼 전치된 배치를 만들 수 없다.

처음 실행하면 페이지에 콜아웃과 표를 자동으로 만들고,
이후에는 같은 블록의 셀 내용만 갱신한다.

필요한 환경변수
  NOTION_TOKEN   : 내부 통합(integration) 토큰
  NOTION_PAGE_ID : 표를 넣을 페이지 ID (데이터베이스 아님)

선택 환경변수
  NOTION_VERSION : API 버전 (기본 2025-09-03)
  NOTION_ALIASES : 지역명 치환 JSON. 예) {"계룡":"A","성주":"B"}
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
PAGE_ID = os.environ.get("NOTION_PAGE_ID", "").strip()
VERSION = os.environ.get("NOTION_VERSION", "2025-09-03").strip()

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": VERSION,
    "Content-Type": "application/json",
}

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(HERE, "..", "data", "weather.json")

# 원본 script.js의 ROW_DEFS와 동일한 순서
ROW_LABELS = [
    "개황",
    "미세먼지 · 미세",
    "미세먼지 · 초미세",
    "기온(℃)",
    "풍향/풍속",
    "일일 누적 강수량",
    "일일 예상 강수량",
    "기상특보",
]

# 원본 WIND_ARROW_MAP을 8방위 화살표로 근사 (Notion은 회전 변환 불가)
WIND_ARROW = {
    "북풍": "↓", "북북동풍": "↓", "북동풍": "↙", "동북동풍": "←",
    "동풍": "←", "동남동풍": "←", "남동풍": "↖", "남남동풍": "↑",
    "남풍": "↑", "남남서풍": "↑", "남서풍": "↗", "서남서풍": "→",
    "서풍": "→", "서북서풍": "→", "북서풍": "↘", "북북서풍": "↓",
}

DUST_COLOR = {"좋음": "blue", "보통": "green", "나쁨": "orange", "매우나쁨": "red"}


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
            time.sleep(float(res.headers.get("Retry-After", 2)))
            continue
        if res.ok:
            return res.json() if res.text else {}
        if 400 <= res.status_code < 500:
            raise RuntimeError(f"{method} {path} → {res.status_code}: {res.text[:400]}")

        last = f"{res.status_code}: {res.text[:200]}"
        time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"{method} {path} 실패 — {last}")


# ------------------------------------------------------------------
# rich_text 헬퍼
# ------------------------------------------------------------------
def t(content, bold=False, color="default"):
    return {
        "type": "text",
        "text": {"content": str(content)[:2000]},
        "annotations": {"bold": bold, "color": color},
    }


def cell(*parts):
    """빈 셀은 '-' 로 채운다 (원본과 동일)"""
    parts = [p for p in parts if p is not None]
    return parts if parts else [t("-", color="gray")]


# ------------------------------------------------------------------
# 항목별 셀 생성 — 원본 renderXXX 함수와 1:1 대응
# ------------------------------------------------------------------
def c_overview(loc):
    v = loc.get("overview", "")
    return cell(t(v) if v and v != "-" else None)


def c_dust(loc, key):
    d = loc.get("dust", {}) or {}
    val = d.get(f"pm{key}", "")
    grade = d.get(f"pm{key}_grade", "")
    if not val or val == "-":
        return cell()
    color = DUST_COLOR.get(grade, "default")
    label = f"{grade} ({val})" if grade and grade != "-" else f"{val} ㎍/㎥"
    parts = [t(label, bold=True, color=color)]
    if d.get("is_fallback"):
        parts.append(t(" !", bold=True, color="orange"))
    return cell(*parts)


def c_temp(loc):
    tp = loc.get("temperature", {}) or {}
    mn, mx, fl = tp.get("min", "-"), tp.get("max", "-"), tp.get("feels_like", "")
    if mn == "-" and mx == "-":
        return cell()
    parts = [
        t(mn, bold=True, color="blue"),
        t(" ~ ", color="gray"),
        t(mx, bold=True, color="red"),
    ]
    if fl and fl != "-":
        parts.append(t(f"\n현재체감 {fl}℃", color="gray"))
    return cell(*parts)


def c_wind(loc):
    w = loc.get("wind", {}) or {}
    d = w.get("direction", "-")
    if not d or d == "-":
        return cell()
    arrow = WIND_ARROW.get(d, "")
    return cell(
        t(f"{arrow} {d}".strip()),
        t(f"\n{w.get('speed', '')}", color="gray"),
    )


def c_rain_acc(loc):
    v = loc.get("rain_accumulated", 0)
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if not v:
        return cell()
    return cell(t(f"{v}mm", bold=True, color="blue"))


def c_rain_fcst(loc):
    rf = loc.get("rain_forecast", []) or []
    if not rf:
        return cell()
    parts = []
    for i, item in enumerate(rf):
        if not isinstance(item, dict):
            continue
        if i:
            parts.append(t("\n"))
        parts.append(t(item.get("time_range", ""), color="gray"))
        parts.append(t(f" {item.get('amount', 0)}mm", bold=True, color="blue"))
    return cell(*parts)


def c_alerts(loc):
    alerts = loc.get("alerts", []) or []
    if not alerts:
        return cell()
    parts = []
    for i, a in enumerate(alerts):
        name = a if isinstance(a, str) else a.get("name", "")
        if not name:
            continue
        if parts:
            parts.append(t("\n"))
        # 원본 alertTagClass: '경보' 포함 → danger(적색), 그 외 → warn(주황)
        color = "red_background" if "경보" in name else "orange_background"
        parts.append(t(name, bold=True, color=color))
        if not isinstance(a, str):
            status = a.get("status", "")
            eff = a.get("effective_time", "")
            tail = " ".join(x for x in (status, eff) if x and x != "발효중")
            if tail:
                parts.append(t(f" {tail}", color="gray"))
    return cell(*parts)


ROW_BUILDERS = [
    c_overview,
    lambda l: c_dust(l, "10"),
    lambda l: c_dust(l, "25"),
    c_temp,
    c_wind,
    c_rain_acc,
    c_rain_fcst,
    c_alerts,
]


# ------------------------------------------------------------------
# 표 구성
# ------------------------------------------------------------------
def build_rows(data, names, display_names):
    locs = data.get("locations", {})

    header = [cell(t("구분", bold=True))]
    header += [cell(t(n, bold=True)) for n in display_names]
    rows = [header]

    for label, build in zip(ROW_LABELS, ROW_BUILDERS):
        row = [cell(t(label, bold=True))]
        for name in names:
            loc = locs.get(name)
            row.append(build(loc) if loc else cell())
        rows.append(row)
    return rows


def table_payload(rows):
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(rows[0]),
            "has_column_header": True,
            "has_row_header": True,
            "children": [
                {"object": "block", "type": "table_row", "table_row": {"cells": r}}
                for r in rows
            ],
        },
    }


def callout_payload(text):
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [t(text)],
            "icon": {"type": "emoji", "emoji": "🌤️"},
            "color": "gray_background",
        },
    }


def get_children(block_id):
    out, cursor = [], None
    while True:
        q = f"?page_size=100{f'&start_cursor={cursor}' if cursor else ''}"
        res = call("GET", f"/blocks/{block_id}/children{q}")
        out.extend(res.get("results", []))
        if not res.get("has_more"):
            return out
        cursor = res.get("next_cursor")
        time.sleep(PAUSE)


# ------------------------------------------------------------------
# 메인
# ------------------------------------------------------------------
def main():
    if not TOKEN or not PAGE_ID:
        print("NOTION_TOKEN / NOTION_PAGE_ID 미설정 — Notion 동기화를 건너뜁니다.")
        return 0

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    locs = data.get("locations", {})
    if not locs:
        print("weather.json에 지역 데이터가 없습니다. 중단합니다.")
        return 1

    names = data.get("location_order") or list(locs.keys())

    aliases = {}
    raw = os.environ.get("NOTION_ALIASES", "").strip()
    if raw:
        try:
            aliases = json.loads(raw)
        except json.JSONDecodeError:
            print("NOTION_ALIASES 파싱 실패 — 원래 지역명을 사용합니다.")
    display_names = [aliases.get(n, n) for n in names]

    rows = build_rows(data, names, display_names)

    # 제목: 원본 updateMeta()와 동일한 형식
    title = f"{data.get('date_display', '')}.({data.get('day_of_week', '')}) 기상예보"
    stamp = f"{data.get('time_display', '')} 기준 · 기상청 · 에어코리아 · 2시간 간격 자동 갱신"

    children = get_children(PAGE_ID)
    tables = [b for b in children if b.get("type") == "table"]
    callouts = [b for b in children if b.get("type") == "callout"]

    # 1) 표 — 있으면 행만 교체, 없거나 크기가 다르면 새로 만든다
    rebuild = True
    if tables:
        tb = tables[0]
        if tb["table"].get("table_width") == len(rows[0]):
            existing = [
                r for r in get_children(tb["id"]) if r.get("type") == "table_row"
            ]
            if len(existing) == len(rows):
                for row_block, cells in zip(existing, rows):
                    call("PATCH", f"/blocks/{row_block['id']}", {"table_row": {"cells": cells}})
                    time.sleep(PAUSE)
                rebuild = False
                print(f"표 갱신 — {len(rows)}행 × {len(rows[0])}열")

        if rebuild:
            for tb in tables:
                call("DELETE", f"/blocks/{tb['id']}")
                time.sleep(PAUSE)

    if rebuild:
        call("PATCH", f"/blocks/{PAGE_ID}/children", {"children": [table_payload(rows)]})
        print(f"표 생성 — {len(rows)}행 × {len(rows[0])}열")

    # 2) 갱신 시각 콜아웃
    if callouts:
        call("PATCH", f"/blocks/{callouts[0]['id']}", {"callout": {"rich_text": [t(stamp)]}})
    else:
        call("PATCH", f"/blocks/{PAGE_ID}/children", {"children": [callout_payload(stamp)]})

    # 3) 페이지 제목
    try:
        call("PATCH", f"/pages/{PAGE_ID}", {"properties": {"title": {"title": [t(title)]}}})
    except RuntimeError as e:
        print(f"제목 갱신 생략: {e}")

    print(f"Notion 동기화 완료 — {title} / {stamp}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Notion 동기화 실패: {e}", file=sys.stderr)
        sys.exit(1)
