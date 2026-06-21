#!/usr/bin/env python3
"""WanderWise 데이터 파이프라인 오케스트레이터.

사용법:
  python run_pipeline.py config/seoul.yaml                 # 전체 실행
  python run_pipeline.py config/seoul.yaml --from generate # 중간부터 재개
  python run_pipeline.py config/seoul.yaml --dry-run       # 적재 없이 확인

각 단계는 멱등: 이미 적재된 external_id는 필터 단계에서 자동 제외.
generation_failed 파일에 남은 장소는 다음 전체 실행 때 다시 수집·시도됨.
"""
import argparse
import json
import sys

from pipeline import collect, embed, filter as flt, generate, load
from pipeline.config import load_config, stage_file

STAGES = ["collect", "filter", "generate", "embed", "load"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--from", dest="start", choices=STAGES, default="collect")
    ap.add_argument("--dry-run", action="store_true", help="load 단계 생략")
    args = ap.parse_args()

    cfg = load_config(args.config)
    start = STAGES.index(args.start)
    places = None

    def resume(stage: str) -> list[dict]:
        f = stage_file(cfg, stage)
        if not f.exists():
            sys.exit(f"중간 파일이 없습니다: {f} — 이전 단계부터 실행하세요.")
        return json.loads(f.read_text(encoding="utf-8"))

    if start <= 0:
        places = collect.run(cfg)
    if start <= 1:
        places = places or resume("collected")
        try:
            ids = load.existing_ids(cfg["city"])
            print(f"[filter] Supabase 기존 {len(ids)}곳 제외 예정")
        except Exception as e:
            print(f"[filter] Supabase 조회 실패({e}) — 기존 데이터 제외 없이 진행")
            ids = set()
        places = flt.run(cfg, places, ids)
    if start <= 2:
        places = places or resume("filtered")
        places = generate.run(cfg, places)
    if start <= 3:
        places = places or resume("generated")
        places = embed.run(cfg, places)
    if start <= 4:
        places = places or resume("embedded")
        if args.dry_run:
            print(f"[load] dry-run — {len(places)}곳 적재 생략")
        else:
            load.run(cfg, places)

    print("\n파이프라인 종료 ✓")


if __name__ == "__main__":
    main()
