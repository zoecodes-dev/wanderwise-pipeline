"""Stage 2 — 필터링 (자동 품질 게이트).

사람이 고르는 대신 규칙이 거른다:
- 프랜차이즈 블랙리스트 (이름) + 동네 N개 이상 반복 (자동 체인 탐지)
- 공공·부속시설 패턴 제외 (시장 문짝, 화장실, 주차장 등)
- 카테고리 블랙리스트 (편의점/미용실/통신사 등)
- 평점·리뷰수 구간 (Google 보강 데이터가 있을 때만)
- 좌표 기반 근접 중복 제거
- 이미 Supabase에 있는 external_id 제외 (멱등성)
"""
import json
import re
from collections import defaultdict

from .config import stage_file

# 공공장소·부속시설 패턴 — reveal에 "광장시장 북2문"이 뜨면 안 됨
FACILITY_PATTERNS = [
    "화장실", "주차장", "관리사무소", "고객지원센터", "고객센터",
    "개방화장실", "공중화장실", "안내소", "매표소", "분수",
    "출입구", "버스정류장", "지하철", "역 ", "주민센터", "동주민",
]
# "○○문", "○○서문/북문/남문" 같은 시장·공원 문짝
GATE_RE = re.compile(r"(서문|남문|동문|북문|정문|후문|[0-9]+문|남[0-9]문|북[0-9]문)$")


def _name_key(name: str) -> str:
    """'하삼동커피 성수점' → '하삼동커피' 로 정규화 (체인 반복 카운트용)."""
    n = name.split()[0] if name.split() else name
    for suffix in ("점", "본점", "직영점"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    return n


def _is_facility(name: str) -> bool:
    if GATE_RE.search(name):
        return True
    return any(pat in name for pat in FACILITY_PATTERNS)


def _grid_key(p: dict) -> tuple:
    """~30m 격자 + 이름 앞 4글자로 근접 중복 판정 (O(n)으로 빠르게)."""
    return (round(p["lat"], 4), round(p["lng"], 4), p["name"][:4])


def _cap_by_neighborhood(places: list[dict], target: int) -> list[dict]:
    """동네별로 골고루 라운드로빈으로 채워서 target개까지만 남김.
    한 동네가 후보를 독식하지 않도록 균형을 맞춘다."""
    if not target or len(places) <= target:
        return places
    buckets = defaultdict(list)
    for p in places:
        buckets[p["neighborhood"]].append(p)
    result, hoods = [], list(buckets.keys())
    i = 0
    while len(result) < target and any(buckets.values()):
        hood = hoods[i % len(hoods)]
        if buckets[hood]:
            result.append(buckets[hood].pop(0))
        i += 1
    return result[:target]


def run(cfg: dict, places: list[dict], existing_ids: set[str]) -> list[dict]:
    f = cfg["filters"]
    target = cfg.get("target_count", 0)
    chain_min_hoods = f.get("chain_min_neighborhoods", 3)  # N개 동네 이상이면 체인

    # --- 사전 패스: 이름별로 몇 개 동네에서 등장하는지 카운트 ---
    hoods_by_name = defaultdict(set)
    for p in places:
        hoods_by_name[_name_key(p["name"])].add(p["neighborhood"])

    kept = []
    rejected = {"franchise": 0, "chain_repeat": 0, "facility": 0, "category": 0,
                "rating": 0, "closed": 0, "duplicate": 0, "already_loaded": 0}
    seen_grid = set()

    for p in places:
        if p["external_id"] in existing_ids:
            rejected["already_loaded"] += 1
            continue
        name = p["name"]
        if any(b in name for b in f["franchise_blacklist"]):
            rejected["franchise"] += 1
            continue
        # 자동 체인 탐지: 같은 이름이 여러 동네에 깔려 있으면 제외
        if len(hoods_by_name[_name_key(name)]) >= chain_min_hoods:
            rejected["chain_repeat"] += 1
            continue
        # 공공·부속시설 (시장 문짝, 화장실, 주차장 등)
        if _is_facility(name):
            rejected["facility"] += 1
            continue
        if any(b in p.get("category_raw", "") for b in f["category_blacklist"]):
            rejected["category"] += 1
            continue
        if p.get("business_status") == "CLOSED_PERMANENTLY":
            rejected["closed"] += 1
            continue
        # 평점·리뷰수 필터: 데이터가 있을 때만 적용 (카카오 단독 수집이면 통과)
        rating, reviews = p.get("rating"), p.get("review_count")
        if rating is not None and rating < f["min_rating"]:
            rejected["rating"] += 1
            continue
        if reviews is not None and not (f["min_reviews"] <= reviews <= f["max_reviews"]):
            rejected["rating"] += 1
            continue
        # 근접 중복 — 격자 해시로 O(1) 판정
        gk = _grid_key(p)
        if gk in seen_grid:
            rejected["duplicate"] += 1
            continue
        seen_grid.add(gk)
        kept.append(p)

    before_cap = len(kept)
    kept = _cap_by_neighborhood(kept, target)

    out = stage_file(cfg, "filtered")
    out.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    msg = f"[filter] {len(places)} → {before_cap}곳 통과"
    if before_cap > len(kept):
        msg += f" → target_count로 {len(kept)}곳 선별 (동네별 균등)"
    print(f"{msg} | 제외 사유: {rejected}")
    return kept
