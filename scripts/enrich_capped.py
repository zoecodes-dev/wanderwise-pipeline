#!/usr/bin/env python3
"""소멸 직전 구글 크레딧을 최종셋에만 알뜰히 쓰는 캡 보강.

- 입력: 게이트 통과한 최종 생성본 (full 레코드 with name/lat/lng/neighborhood/category)
- 동네 라운드로빈 + 음식/술 카테고리 우선으로 우선순위 큐 구성
- BUDGET 콜까지만 google_enrich (평점/리뷰/영업시간/폐업여부)
- CLOSED_PERMANENTLY 는 제거
- 보강 못 받은 곳은 그대로 둠 (구글 필드 없음)

사용: PYTHONPATH=. python scripts/enrich_capped.py <in.json> <out.json> [budget]
"""
import json
import sys
import time
from collections import defaultdict

from pipeline.config import env
from pipeline import collect

HOURS_FIRST = ["카페", "베이커리", "바", "노포", "시장"]  # 영업시간이 중요한 카테고리

def main():
    inp, outp = sys.argv[1], sys.argv[2]
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 2300
    key = env("GOOGLE_API_KEY")
    places = json.load(open(inp))

    # 우선순위: 동네 라운드로빈, 각 동네 안에서 음식/술 우선
    by_hood = defaultdict(list)
    for p in places:
        by_hood[p["neighborhood"]].append(p)
    def catrank(p): return HOURS_FIRST.index(p["category"]) if p["category"] in HOURS_FIRST else len(HOURS_FIRST)
    for h in by_hood:
        by_hood[h].sort(key=catrank)
    order, hoods = [], list(by_hood.keys())
    i = 0
    while any(by_hood.values()):
        h = hoods[i % len(hoods)]
        if by_hood[h]:
            order.append(by_hood[h].pop(0))
        i += 1

    calls = 0
    closed = []
    enriched = 0
    for p in order:
        if calls >= budget:
            break
        data = collect.google_enrich(p["name"], p["lat"], p["lng"], key)
        calls += 1
        if data:
            p.update(data)
            enriched += 1
            if p.get("business_status") == "CLOSED_PERMANENTLY":
                closed.append(p["external_id"])
        if calls % 100 == 0:
            print(f"[enrich] {calls}/{budget} 콜 (보강성공 {enriched}, 폐업 {len(closed)})")
        time.sleep(0.08)

    kept = [p for p in places if p["external_id"] not in set(closed)]
    json.dump(kept, open(outp, "w"), ensure_ascii=False, indent=2)
    print(f"\n[enrich] 콜 {calls} 사용 | 보강 {enriched}곳 | 폐업제거 {len(closed)}곳")
    print(f"[enrich] 최종 {len(kept)}곳 → {outp}")
    print(f"[enrich] 미보강(구글필드 없음): {len(kept)-enriched}곳")

if __name__ == "__main__":
    main()
