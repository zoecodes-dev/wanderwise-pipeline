#!/usr/bin/env python3
"""진단 전용 스크립트 — 파이프라인을 건드리지 않음.

하는 일:
1) data/seoul_collected.json (수집 원본)을 읽음
2) 각 장소에 Google Places 리뷰수/평점을 붙임 (샘플 또는 전체)
3) 리뷰수 분포를 출력 → max_reviews 상한 정하는 근거
4) 같은 이름이 여러 동네에서 반복되는 정도 출력 → 체인 탐지 근거

실행:
  python diagnose_chains.py                 # 기본: 400곳 샘플만 (Google 호출 아낌)
  python diagnose_chains.py --all           # 전체 보강 (느리고 호출 많음)
  python diagnose_chains.py --limit 800     # 샘플 개수 조절

결과는 data/seoul_diagnosis.json 에도 저장됨.
"""
import argparse
import json
import time
from collections import Counter, defaultdict

import requests
from dotenv import load_dotenv

from pipeline.config import env, stage_file, load_config

load_dotenv()
GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"


def google_lookup(name: str, lat: float, lng: float, api_key: str) -> dict:
    try:
        resp = requests.post(
            GOOGLE_URL,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.rating,places.userRatingCount,places.displayName",
            },
            json={
                "textQuery": name,
                "locationBias": {"circle": {
                    "center": {"latitude": lat, "longitude": lng}, "radius": 150.0}},
                "languageCode": "ko",
                "pageSize": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if not places:
            return {}
        return {
            "g_rating": places[0].get("rating"),
            "g_reviews": places[0].get("userRatingCount"),
        }
    except Exception:
        return {}


def name_key(name: str) -> str:
    """'하삼동커피 성수점' → '하삼동커피' 로 정규화해서 반복 카운트."""
    n = name.split()[0] if name.split() else name
    for suffix in ("점", "본점", "직영점"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n


def bucket(reviews: int | None) -> str:
    if reviews is None:
        return "데이터없음"
    for lo, hi in [(0, 50), (50, 100), (100, 300), (300, 500),
                   (500, 1000), (1000, 2000), (2000, 5000)]:
        if reviews < hi:
            return f"{lo}~{hi}"
    return "5000+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    cfg = load_config("config/seoul.yaml")
    src = stage_file(cfg, "collected")
    if not src.exists():
        raise SystemExit(f"{src} 없음 — 먼저 수집(collect)을 돌려야 합니다.")
    places = json.loads(src.read_text(encoding="utf-8"))
    print(f"수집 원본: {len(places)}곳")

    # --- 체인 탐지: 정규화 이름이 몇 개 '동네'에서 나오는지 ---
    hoods_by_name = defaultdict(set)
    count_by_name = Counter()
    for p in places:
        k = name_key(p["name"])
        hoods_by_name[k].add(p["neighborhood"])
        count_by_name[k] += 1

    print("\n=== 여러 동네에 반복 등장하는 이름 (체인 의심) ===")
    print("형식: 이름  |  등장 동네 수  |  총 등장 횟수")
    repeated = sorted(
        [(k, len(h), count_by_name[k]) for k, h in hoods_by_name.items() if len(h) >= 2],
        key=lambda x: (-x[1], -x[2]),
    )
    for k, nhoods, total in repeated[:40]:
        print(f"  {k:20s} | {nhoods}개 동네 | {total}회")
    if not repeated:
        print("  (2개 이상 동네에서 반복되는 이름 없음)")

    # --- 리뷰수 보강 (샘플 또는 전체) ---
    api_key = env("GOOGLE_API_KEY")
    targets = places if args.all else places[: args.limit]
    print(f"\nGoogle 리뷰수 보강 중… ({len(targets)}곳)")
    for i, p in enumerate(targets):
        p.update(google_lookup(p["name"], p["lat"], p["lng"], api_key))
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(targets)}")
        time.sleep(0.05)

    # --- 리뷰수 분포 ---
    dist = Counter(bucket(p.get("g_reviews")) for p in targets)
    order = ["0~50", "50~100", "100~300", "300~500", "500~1000",
             "1000~2000", "2000~5000", "5000+", "데이터없음"]
    print("\n=== 리뷰수 분포 ===")
    for b in order:
        if dist.get(b):
            bar = "█" * min(40, dist[b])
            print(f"  {b:12s} {dist[b]:4d}  {bar}")

    # --- 유명 장소 TOP (상한 정하는 데 참고) ---
    rated = [p for p in targets if p.get("g_reviews")]
    rated.sort(key=lambda p: -p["g_reviews"])
    print("\n=== 리뷰 많은 곳 TOP 20 (이 중 어디까지 '너무 유명'인지 보세요) ===")
    for p in rated[:20]:
        print(f"  {p['g_reviews']:6d}리뷰 | ⭐{p.get('g_rating','-')} | "
              f"{p['name']} ({p['neighborhood']})")

    out = stage_file(cfg, "diagnosis")
    out.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out}")
    print("\n다음: 위 TOP 목록에서 '빼고 싶은 곳'의 리뷰수를 보고 max_reviews 상한을,")
    print("     반복 등장 목록에서 '몇 개 동네 이상이면 체인'인지 임계값을 정하면 됩니다.")


if __name__ == "__main__":
    main()
