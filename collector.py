"""
기상자료 수집 모듈 (8개 지역)

수집 원천
  기상청 단기예보 조회서비스        getVilageFcst / getUltraSrtNcst
  기상청 단기예보 통보문 조회서비스  getWthrSituation
  기상청 기상특보 조회서비스        getWthrWrnList / getWthrWrnMsg
  한국환경공단 에어코리아           getCtprvnRltmMesureDnsty (+ 측정소 자동탐색)
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
STATE_DIR = Path(os.getenv("STATE_DIR", BASE_DIR / "state"))
PUBLIC_DIR = Path(os.getenv("PUBLIC_DIR", BASE_DIR / "public"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
KST = timezone(timedelta(hours=9))

COMMON_KEY = os.getenv("DATA_GO_KR_KEY", "")
KMA_KEY = os.getenv("KMA_SERVICE_KEY", "") or COMMON_KEY
AIR_KEY = os.getenv("AIRKOREA_SERVICE_KEY", "") or COMMON_KEY

KMA_VILAGE = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
KMA_MSG = "http://apis.data.go.kr/1360000/VilageFcstMsgService"
KMA_WARN = "http://apis.data.go.kr/1360000/WthrWrnInfoService"
AIR_OBS = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
AIR_STN = "http://apis.data.go.kr/B552584/MsrstnInfoInqireSvc"

TIMEOUT = 15
FORECAST_END_HOUR = 9       # 예상 강수량 집계 종료: 익일 09시

REGIONS = json.loads((BASE_DIR / "regions.json").read_text(encoding="utf-8"))


# ── 위경도 → 기상청 5km 격자 ────────────────────────────────
def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT, XO, YO = 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0
    re_ = RE / GRID
    s1, s2, ol, oa = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.tan(math.pi * .25 + s2 * .5) / math.tan(math.pi * .25 + s1 * .5)
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(sn)
    sf = math.tan(math.pi * .25 + s1 * .5)
    sf = (sf ** sn) * math.cos(s1) / sn
    ro = math.tan(math.pi * .25 + oa * .5)
    ro = re_ * sf / (ro ** sn)
    ra = math.tan(math.pi * .25 + lat * D * .5)
    ra = re_ * sf / (ra ** sn)
    th = lon * D - ol
    th = th - 2 * math.pi if th > math.pi else th + 2 * math.pi if th < -math.pi else th
    th *= sn
    return int(ra * math.sin(th) + XO + .5), int(ro - ra * math.cos(th) + YO + .5)


# ── 공통 호출 ───────────────────────────────────────────────
def _get(url: str, params: dict, key: str) -> list[dict]:
    if not key:
        raise RuntimeError("서비스키 미설정 (.env 확인)")
    p = {"serviceKey": key, "dataType": "JSON", "pageNo": 1, "numOfRows": 100, **params}
    r = requests.get(url, params=p, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.text.lstrip().startswith("{"):
        raise RuntimeError(f"비정상 응답: {r.text[:160]}")
    resp = r.json().get("response", {})
    hdr = resp.get("header", {})
    code = hdr.get("resultCode")
    if code not in ("00", "0", None):
        raise RuntimeError(f"[{code}] {hdr.get('resultMsg')}")
    items = (resp.get("body") or {}).get("items") or {}
    if isinstance(items, dict):
        items = items.get("item") or []
    return items if isinstance(items, list) else [items]


def parse_mm(v) -> float:
    """'강수없음' / '1.0mm 미만' / '30.0~50.0mm' / '3.5mm' → mm"""
    if v in (None, ""):
        return 0.0
    s = str(v).strip()
    if s in ("강수없음", "적설없음", "-", "0", "0.0"):
        return 0.0
    if "미만" in s:
        return 0.5
    if "~" in s:
        s = s.split("~")[0]
    n = "".join(c for c in s if c.isdigit() or c == ".")
    try:
        return float(n)
    except ValueError:
        return 0.0


DIR8 = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]
SKY_TXT = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY_TXT = {"0": "", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기",
           "5": "빗방울", "6": "진눈깨비", "7": "눈날림"}


def wind_name(deg: float) -> str:
    return DIR8[int((float(deg) + 22.5) / 45.0) % 8] + "풍"


def vilage_base(now: datetime) -> tuple[str, str]:
    t = now - timedelta(minutes=15)
    for h in (23, 20, 17, 14, 11, 8, 5, 2):
        if t.hour >= h:
            return t.strftime("%Y%m%d"), f"{h:02d}00"
    return (t - timedelta(days=1)).strftime("%Y%m%d"), "2300"


# ── 일 누적 강수량: 초단기실황 RN1 시간별 누적 (결측 자동 보정) ──
RAIN_FILE = STATE_DIR / "rain.json"
_rain_lock = threading.Lock()


def _rain_load() -> dict:
    if RAIN_FILE.exists():
        try:
            return json.loads(RAIN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def rain_accum(region_id: str, nx: int, ny: int, now: datetime) -> dict:
    """오늘 00시부터 기준시각까지의 시간별 RN1 을 채워 넣고 합산."""
    today = now.strftime("%Y%m%d")
    # 초단기실황은 매시 정각 관측이 40분 이후 제공된다
    last = (now - timedelta(minutes=45)).replace(minute=0, second=0, microsecond=0)
    if last.strftime("%Y%m%d") != today:
        last = None

    with _rain_lock:
        store = _rain_load()
        day = store.setdefault(region_id, {}).setdefault(today, {})

    if last is not None:
        for h in range(last.hour + 1):
            key = f"{h:02d}00"
            if key in day:
                continue
            try:
                items = _get(f"{KMA_VILAGE}/getUltraSrtNcst",
                             {"base_date": today, "base_time": key,
                              "nx": nx, "ny": ny, "numOfRows": 60}, KMA_KEY)
                v = {i["category"]: i["obsrValue"] for i in items}
                day[key] = parse_mm(v.get("RN1"))
            except Exception:
                pass  # 다음 갱신 때 재시도

    with _rain_lock:
        store = _rain_load()
        store.setdefault(region_id, {})[today] = day
        for d in list(store[region_id]):
            if d < today:
                store[region_id].pop(d)
        RAIN_FILE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")

    hours = sorted(day)
    return {
        "mm": round(sum(day.values()), 1),
        "hours": len(hours),
        "lastHour": hours[-1] if hours else None,
        "missing": [f"{h:02d}00" for h in range((last.hour + 1) if last else 0)
                    if f"{h:02d}00" not in day],
    }


# ── 실황 ────────────────────────────────────────────────────
def fetch_ncst(nx: int, ny: int, now: datetime) -> dict:
    t = now - timedelta(minutes=45)
    items = _get(f"{KMA_VILAGE}/getUltraSrtNcst",
                 {"base_date": t.strftime("%Y%m%d"), "base_time": t.strftime("%H00"),
                  "nx": nx, "ny": ny, "numOfRows": 60}, KMA_KEY)
    v = {i["category"]: i["obsrValue"] for i in items}
    f = lambda k: float(v[k]) if v.get(k) not in (None, "") else None
    return {"temp": f("T1H"), "humidity": f("REH"),
            "windDeg": f("VEC"), "windSpeed": f("WSD"),
            "obsTime": f"{t:%Y-%m-%d %H}:00"}


# ── 단기예보 ────────────────────────────────────────────────
def fetch_forecast(nx: int, ny: int, now: datetime) -> dict:
    base = now.replace(minute=0, second=0, microsecond=0)
    end = (base + timedelta(days=1)).replace(hour=FORECAST_END_HOUR)
    today = base.strftime("%Y%m%d")
    bd, bt = vilage_base(now)

    rows = _get(f"{KMA_VILAGE}/getVilageFcst",
                {"base_date": bd, "base_time": bt, "nx": nx, "ny": ny, "numOfRows": 1000},
                KMA_KEY)

    # (fcstDate, fcstTime) → {category: value}
    grid: dict[tuple[str, str], dict] = {}
    for it in rows:
        grid.setdefault((it["fcstDate"], it["fcstTime"]), {})[it["category"]] = it["fcstValue"]

    # TMN/TMX 는 새벽 발표분에만 실린다 → 오늘 02시 발표로 보완
    tmn = tmx = None
    for (d, _), c in grid.items():
        if d == today:
            tmn = tmn if "TMN" not in c else float(c["TMN"])
            tmx = tmx if "TMX" not in c else float(c["TMX"])
    if (tmn is None or tmx is None) and bt != "0200":
        try:
            for it in _get(f"{KMA_VILAGE}/getVilageFcst",
                           {"base_date": today, "base_time": "0200",
                            "nx": nx, "ny": ny, "numOfRows": 1000}, KMA_KEY):
                if it["fcstDate"] == today and it["category"] == "TMN" and tmn is None:
                    tmn = float(it["fcstValue"])
                if it["fcstDate"] == today and it["category"] == "TMX" and tmx is None:
                    tmx = float(it["fcstValue"])
        except Exception:
            pass

    # 기준시각 이후 ~ 익일 09시 구간
    window = []
    for (d, t), c in sorted(grid.items()):
        dt = datetime.strptime(d + t, "%Y%m%d%H%M").replace(tzinfo=KST)
        if base < dt <= end:
            window.append((dt, c))

    # 시간대별 예상 강수량 → 연속 구간으로 묶기
    blocks, cur = [], None
    for dt, c in window:
        mm = parse_mm(c.get("PCP"))
        if mm > 0:
            if cur is None:
                cur = {"from": dt, "to": dt, "mm": mm}
            else:
                cur["to"], cur["mm"] = dt, cur["mm"] + mm
        elif cur is not None:
            blocks.append(cur)
            cur = None
    if cur is not None:
        blocks.append(cur)

    rain_blocks = [{
        "start": b["from"].strftime("%H:%M"),
        "end": (b["to"] + timedelta(hours=1)).strftime("%H:%M"),
        "startDay": "내일" if b["from"].date() != base.date() else "오늘",
        "mm": round(b["mm"], 1),
    } for b in blocks]

    # 개황: 기준시각 하늘상태 + 이후 강수 예고
    head = window[0][1] if window else {}
    sky = SKY_TXT.get(str(head.get("SKY")), "-")
    pty_now = PTY_TXT.get(str(head.get("PTY")), "")
    later = next((PTY_TXT.get(str(c.get("PTY")), "") for _, c in window
                  if PTY_TXT.get(str(c.get("PTY")), "")), "")
    summary = pty_now or sky
    note = f"이후 {later}" if (later and not pty_now) else None

    # 풍향/풍속: 기준시각 이후 6시간
    near = window[:6] or window
    degs = [float(c["VEC"]) for _, c in near if c.get("VEC") not in (None, "")]
    spds = [float(c["WSD"]) for _, c in near if c.get("WSD") not in (None, "")]
    if degs:
        rad = [math.radians(d) for d in degs]
        mean = math.degrees(math.atan2(sum(math.sin(r) for r in rad) / len(rad),
                                       sum(math.cos(r) for r in rad) / len(rad))) % 360
    else:
        mean = None

    return {
        "tempMin": tmn, "tempMax": tmx,
        "summary": summary, "summaryNote": note,
        "rainBlocks": rain_blocks,
        "rainTotal": round(sum(b["mm"] for b in rain_blocks), 1),
        "popMax": max((int(c["POP"]) for _, c in window if c.get("POP")), default=None),
        "windDeg": round(mean, 1) if mean is not None else None,
        "windName": wind_name(mean) if mean is not None else None,
        "windMin": round(min(spds), 1) if spds else None,
        "windMax": round(max(spds), 1) if spds else None,
        "baseTime": f"{bd[4:6]}.{bd[6:8]} {bt[:2]}:00",
        "windowEnd": end.strftime("%m.%d %H:%M"),
    }


# ── 개황 통보문 (관서 단위, 재사용) ──────────────────────────
def fetch_situation(stn_id: str) -> dict:
    items = _get(f"{KMA_MSG}/getWthrSituation", {"stnId": stn_id, "numOfRows": 3}, KMA_KEY)
    if not items:
        return {}
    it = items[0]
    return {"text": (it.get("wfSv1") or it.get("wfSv") or "").strip() or None,
            "announced": it.get("tmFc")}


# ── 기상특보 (관서 단위로 받아 시·군명으로 필터) ─────────────
WARN_LINE = re.compile(r"([가-힣]{2,10}(?:주의보|경보))\s*[:：]\s*(.+)")


def fetch_warn_text(stn_id: str, now: datetime) -> str:
    frm = (now - timedelta(days=2)).strftime("%Y%m%d")
    to = now.strftime("%Y%m%d")
    rows = _get(f"{KMA_WARN}/getWthrWrnList",
                {"stnId": stn_id, "fromTmFc": frm, "toTmFc": to, "numOfRows": 20}, KMA_KEY)
    rows = sorted(rows, key=lambda r: (str(r.get("tmFc")), str(r.get("tmSeq"))), reverse=True)
    for r in rows[:3]:
        try:
            msgs = _get(f"{KMA_WARN}/getWthrWrnMsg",
                        {"stnId": stn_id, "tmFc": r.get("tmFc"), "tmSeq": r.get("tmSeq")}, KMA_KEY)
        except Exception:
            continue
        for m in msgs:
            body = "\n".join(str(m.get(f"t{i}") or "") for i in range(1, 9))
            if WARN_LINE.search(body):
                return body
    return ""


def parse_warnings(body: str, keywords: list[str]) -> list[dict]:
    """'o 폭염경보 : 대구, 경상북도(경산, 영천 ...)' 형태에서 해당 시·군만 추린다."""
    out, seen = [], set()
    for line in body.splitlines():
        if "해제" in line or "예비" in line:
            continue
        m = WARN_LINE.search(line)
        if not m:
            continue
        name, area = m.group(1), m.group(2)
        if any(k in area for k in keywords) and name not in seen:
            seen.add(name)
            out.append({"name": name, "level": "경보" if name.endswith("경보") else "주의보"})
    return out


# ── 미세먼지 ────────────────────────────────────────────────
STATION_FILE = STATE_DIR / "stations.json"


def resolve_station(region: dict) -> str | None:
    """umdName → TM 좌표 → 근접 측정소. 결과는 디스크에 캐시."""
    if region.get("airStation"):
        return region["airStation"]
    cache = json.loads(STATION_FILE.read_text(encoding="utf-8")) if STATION_FILE.exists() else {}
    if region["id"] in cache:
        return cache[region["id"]]
    try:
        cands = _get(f"{AIR_STN}/getTMStdrCrdnt",
                     {"umdName": region["umdName"], "returnType": "json", "numOfRows": 30}, AIR_KEY)
        row = next((c for c in cands if region["sggName"] in str(c.get("sggName", ""))), None) \
            or (cands[0] if cands else None)
        if not row:
            return None
        stns = _get(f"{AIR_STN}/getNearbyMsrstnList",
                    {"tmX": row["tmX"], "tmY": row["tmY"], "returnType": "json", "numOfRows": 5},
                    AIR_KEY)
        name = stns[0].get("stationName") if stns else None
        if name:
            cache[region["id"]] = name
            STATION_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return name
    except Exception:
        return None


def fetch_air_sido(sido: str) -> list[dict]:
    return _get(f"{AIR_OBS}/getCtprvnRltmMesureDnsty",
                {"sidoName": sido, "ver": "1.3", "returnType": "json", "numOfRows": 300}, AIR_KEY)


PM10_T, PM25_T = (30, 80, 150), (15, 35, 75)
GRADE_TXT = {1: "좋음", 2: "보통", 3: "나쁨", 4: "매우나쁨"}


def grade(v: float | None, t: tuple) -> int:
    if v is None:
        return 0
    return 1 if v <= t[0] else 2 if v <= t[1] else 3 if v <= t[2] else 4


def pick_air(rows: list[dict], station: str | None) -> dict:
    row = next((r for r in rows if r.get("stationName") == station), None) if station else None
    if row is None:
        return {"station": station}
    num = lambda x: (float(x) if str(x).replace(".", "", 1).isdigit() else None)
    pm10, pm25 = num(row.get("pm10Value")), num(row.get("pm25Value"))
    g10, g25 = grade(pm10, PM10_T), grade(pm25, PM25_T)
    return {"station": row.get("stationName"), "obsTime": row.get("dataTime"),
            "pm10": pm10, "pm10Grade": g10, "pm10Text": GRADE_TXT.get(g10),
            "pm25": pm25, "pm25Grade": g25, "pm25Text": GRADE_TXT.get(g25)}


# ── 수집 ────────────────────────────────────────────────────
def collect(now: datetime) -> dict:
    memo_sit, memo_warn, memo_air = {}, {}, {}
    regions = []

    for rg in REGIONS:
        nx, ny = latlon_to_grid(rg["lat"], rg["lon"])
        out = {"id": rg["id"], "short": rg["short"], "label": rg["label"],
               "grid": {"nx": nx, "ny": ny}, "errors": {}}

        def run(name, fn):
            try:
                out[name] = fn()
            except Exception as e:
                out[name] = {} if name != "warnings" else []
                out["errors"][name] = str(e)[:160]

        run("forecast", lambda: fetch_forecast(nx, ny, now))
        run("current", lambda: fetch_ncst(nx, ny, now))
        run("rain", lambda: rain_accum(rg["id"], nx, ny, now))

        def situation():
            if rg["wrnStnId"] not in memo_sit:
                memo_sit[rg["wrnStnId"]] = fetch_situation(rg["wrnStnId"])
            return memo_sit[rg["wrnStnId"]]
        run("situation", situation)

        def warnings():
            if rg["wrnStnId"] not in memo_warn:
                memo_warn[rg["wrnStnId"]] = fetch_warn_text(rg["wrnStnId"], now)
            return parse_warnings(memo_warn[rg["wrnStnId"]], rg["warnKeywords"])
        run("warnings", warnings)

        def air():
            if rg["sidoName"] not in memo_air:
                memo_air[rg["sidoName"]] = fetch_air_sido(rg["sidoName"])
            return pick_air(memo_air[rg["sidoName"]], resolve_station(rg))
        run("air", air)

        regions.append(out)

    base = now.replace(minute=0, second=0, microsecond=0)
    wd = "월화수목금토일"[base.weekday()]
    return {
        "updatedAt": now.isoformat(timespec="seconds"),
        "baseLabel": f"{base.month}.{base.day} ({wd}) 기상예보",
        "baseTime": base.strftime("%H:%M"),
        "windowEnd": (base + timedelta(days=1)).replace(hour=FORECAST_END_HOUR).strftime("%m.%d %H:%M"),
        "regions": regions,
    }


# ── 저장 ────────────────────────────────────────────────────
def write_output(payload: dict) -> Path:
    out = PUBLIC_DIR / "data.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def run_once() -> dict:
    now = datetime.now(KST)
    payload = collect(now)
    write_output(payload)
    bad = sum(len(r["errors"]) for r in payload["regions"])
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {payload['baseLabel']} {payload['baseTime']} 기준 "
          f"— {len(payload['regions'])}개 지역, 실패 {bad}건")
    for r in payload["regions"]:
        for k, v in r["errors"].items():
            print(f"  ! {r['short']} {k}: {v}")
    return payload
