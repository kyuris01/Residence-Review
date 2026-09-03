# -*- coding: utf-8 -*-
"""
run_all.py — 배치 3개를 순서대로 실행한다.

②(수집) 또는 ③(요약)이 실패해도 ①(정량)만으로 data.json 을 만들어 두고 끝낸다.
기획서의 '①만 끝나도 배포할 산출물이 남는다'는 원칙을 그대로 코드로 옮긴 것.

실행:  python scripts/run_all.py
       python scripts/run_all.py --no-youtube      (P1 생략)
       python scripts/run_all.py --quant-only      (①③만, 후기 없이)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(name: str, extra: list[str]) -> bool:
    print("\n" + "=" * 64)
    print("▶ {0}".format(name))
    print("=" * 64)
    proc = subprocess.run([sys.executable, str(HERE / name)] + extra)
    ok = proc.returncode == 0
    if not ok:
        print("[실패] {0} (exit {1})".format(name, proc.returncode))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-youtube", action="store_true")
    ap.add_argument("--quant-only", action="store_true", help="후기 수집·요약을 건너뛴다")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    limit = ["--limit", str(args.limit)] if args.limit else []

    if not run("base.py", limit):
        print("\n① 이 실패해 더 진행하지 않습니다. API 키와 엔드포인트를 확인하세요.")
        sys.exit(1)

    if args.quant_only:
        run("summarize.py", ["--no-llm"])
        return

    collected = run("collect.py", limit + (["--no-youtube"] if args.no_youtube else []))
    if not collected:
        print("\n② 가 실패했지만 ①의 정량 데이터로 화면은 채웁니다.")
    run("summarize.py", limit if collected else ["--no-llm"])


if __name__ == "__main__":
    main()
