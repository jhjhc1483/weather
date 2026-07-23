"""로컬 실행용 서버. 매시 05분에 수집하고 public/ 을 그대로 서빙한다.

    pip install -r requirements-server.txt
    python server.py     →  http://localhost:8000
"""
import threading
import time
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from collector import KST, PUBLIC_DIR, run_once

REFRESH_MINUTE = 5
_cache: dict = {}


def refresh():
    global _cache
    _cache = run_once()
    return _cache


def scheduler():
    while True:
        now = datetime.now(KST)
        nxt = now.replace(minute=REFRESH_MINUTE, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(hours=1)
        time.sleep(max((nxt - datetime.now(KST)).total_seconds(), 1))
        try:
            refresh()
        except Exception as e:
            print("갱신 실패:", e)


app = FastAPI(title="기상예보 상황판")


@app.on_event("startup")
def startup():
    threading.Thread(target=refresh, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()


@app.get("/api/weather")
def api_weather():
    return _cache or JSONResponse({"pending": True, "regions": []}, status_code=202)


@app.post("/api/refresh")
def api_refresh():
    return refresh()


app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
