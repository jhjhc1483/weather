#!/usr/bin/env python3
"""1회 수집 → public/data.json + state/ 갱신. GitHub Actions 가 매시 실행한다."""
import sys
from collector import run_once

if __name__ == "__main__":
    payload = run_once()
    # 전 지역 전 항목이 실패했다면 워크플로를 붉게 표시한다
    if all(len(r["errors"]) >= 5 for r in payload["regions"]):
        print("모든 지역 수집 실패 — 인증키/트래픽을 확인하세요", file=sys.stderr)
        sys.exit(1)
