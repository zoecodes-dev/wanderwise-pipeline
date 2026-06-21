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


# 리뷰가 잘 쌓이는 카테고리 — 여기만 min_reviews 하한 적용.
# 공방·갤러리·독립서점·노포·공원·전망·시장은 리뷰 문화가 달라 하한 없음
# (리뷰 적은 동네 공방이야말로 serendipity의 핵심이므로 죽이면 안 됨).
REVIEW_RICH_CATEGORIES = {"카페", "베이커리", "바"}


def _passes_reviews(p: dict, f: dict) -> bool:
    """카테고리별 리뷰 기준. 리뷰 데이터 없으면 통과(카카오 단독 수집 대비)."""
    reviews = p.get("review_count")
    if reviews is None:
        return True
    if reviews > f["max_reviews"]:          # 너무 유명한 건 카테고리 무관 제외
        return False
    if p.get("category") in REVIEW_RICH_CATEGORIES:
        return reviews >= f["min_reviews"]  # 카페류만 하한 적용
    return True                             # 공방·갤러리 등은 하한 면제


def _select_balanced(places: list[dict], target: int, max_cafe_ratio: float = 0.35) -> list[dict]:
    """동네 + 카테고리 양쪽으로 골고루 선별.
    카페(리뷰리치)와 비카페를 처음부터 함께 라운드로빈으로 뽑되, 각 카테고리
    안에서는 동네가 겹치지 않게 순환한다. 카페는 max_cafe_ratio를 '상한'으로만
    제한 — 비카페를 먼저 다 채워 카페가 0이 되던 문제를 막고, 카페가 전체의
    20~35% 수준으로 자연스럽게 섞이게 한다."""
    if not target or len(places) <= target:
        return places

    cafe_cap = int(target * max_cafe_ratio)

    # 카테고리 → 동네별 큐 + 카테고리마다 동네 순환 포인터
    by_cat = defaultdict(lambda: defaultdict(list))
    for p in places:
        by_cat[p["category"]][p["neighborhood"]].append(p)
    cat_hood_order = {cat: list(hoods.keys()) for cat, hoods in by_cat.items()}
    cat_ptr = defaultdict(int)

    def pop_one(cat: str):
        """해당 카테고리에서 동네를 순환하며 한 곳 꺼냄 (동네 균등 유지)."""
        order = cat_hood_order.get(cat, [])
        if not order:
            return None
        for _ in range(len(order)):
            h = order[cat_ptr[cat] % len(order)]
            cat_ptr[cat] += 1
            if by_cat[cat][h]:
                return by_cat[cat][h].pop(0)
        return None

    cafe_cats = [c for c in by_cat if c in REVIEW_RICH_CATEGORIES]
    noncafe_cats = [c for c in by_cat if c not in REVIEW_RICH_CATEGORIES]
    grp_ptr = {"cafe": 0, "noncafe": 0}

    def pop_group(group: str):
        """그룹(카페/비카페) 안의 카테고리를 순환하며 한 곳 꺼냄."""
        cats = cafe_cats if group == "cafe" else noncafe_cats
        for _ in range(len(cats)):
            cat = cats[grp_ptr[group] % len(cats)]
            grp_ptr[group] += 1
            p = pop_one(cat)
            if p:
                return p
        return None

    result = []
    cafe_count = 0
    while len(result) < target:
        # 카페는 상한 미만이고 지금까지 비율이 상한 아래일 때만 섞어 넣는다.
        # (len==0인 첫 픽은 카페부터 — 이후 비율이 상한을 넘으면 비카페로 균형)
        take_cafe = cafe_count < cafe_cap and cafe_count <= max_cafe_ratio * len(result)
        p = pop_group("cafe" if take_cafe else "noncafe")
        if p is None:
            # 원하던 그룹이 비었으면 반대쪽에서 채운다 (카페는 상한을 넘기지 않음)
            if take_cafe:
                p = pop_group("noncafe")
            elif cafe_count < cafe_cap:
                p = pop_group("cafe")
            if p is None:
                break  # 양쪽 모두 소진
        if p["category"] in REVIEW_RICH_CATEGORIES:
            cafe_count += 1
        result.append(p)

    return result[:target]


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
        # 평점 필터: 데이터가 있을 때만 적용
        rating = p.get("rating")
        if rating is not None and rating < f["min_rating"]:
            rejected["rating"] += 1
            continue
        # 리뷰수 필터: 카테고리별 차등 (카페류만 하한, 공방·갤러리 등은 면제)
        if not _passes_reviews(p, f):
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
    kept = _select_balanced(kept, target, max_cafe_ratio=f.get("max_cafe_ratio", 0.35))

    out = stage_file(cfg, "filtered")
    out.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    cat_mix = dict(Counter(p.get("category") for p in kept))
    msg = f"[filter] {len(places)} → {before_cap}곳 통과"
    if before_cap > len(kept):
        msg += f" → {len(kept)}곳 선별 (동네+카테고리 균형)"
    print(f"{msg} | 제외 사유: {rejected}")
    print(f"[filter] 선별 카테고리 분포: {cat_mix}")
    return kept
