"""Stage 1 — 수집.

카카오 로컬 API로 (동네 별칭 × 카테고리) 조합을 전부 검색.
enrich_with_google=true면 Google Places로 평점/리뷰수/영업시간 보강.
"""
import json
import time
import requests

from .config import env, stage_file

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"


def kakao_search(query: str, lat: float, lng: float, radius: int, api_key: str) -> list[dict]:
    """키워드 검색, 최대 45개(15×3페이지)."""
    results = []
    for page in range(1, 4):
        resp = requests.get(
            KAKAO_URL,
            headers={"Authorization": f"KakaoAK {api_key}"},
            params={
                "query": query,
                "x": lng, "y": lat,
                "radius": radius,
                "page": page, "size": 15,
                "sort": "accuracy",
            },
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        results.extend(body.get("documents", []))
        if body.get("meta", {}).get("is_end", True):
            break
        time.sleep(0.2)
    return results


def google_enrich(name: str, lat: float, lng: float, api_key: str) -> dict:
    """이름+좌표로 Google Places 매칭해서 평점/영업시간 가져오기. 실패하면 빈 dict."""
    try:
        resp = requests.post(
            GOOGLE_URL,
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": (
                    "places.rating,places.userRatingCount,"
                    "places.priceLevel,places.regularOpeningHours,places.businessStatus"
                ),
            },
            json={
                "textQuery": name,
                "locationBias": {"circle": {
                    "center": {"latitude": lat, "longitude": lng}, "radius": 150.0,
                }},
                "languageCode": "ko",
                "pageSize": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if not places:
            return {}
        p = places[0]
        return {
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"),
            "price_level": p.get("priceLevel"),
            "opening_hours": p.get("regularOpeningHours", {}).get("weekdayDescriptions"),
            "business_status": p.get("businessStatus"),
        }
    except Exception:
        return {}


def run(cfg: dict) -> list[dict]:
    api_key = env("KAKAO_API_KEY")
    google_key = env("GOOGLE_API_KEY", required=False) if cfg.get("enrich_with_google") else None
    radius = cfg["filters"]["search_radius_m"]

    raw: dict[str, dict] = {}  # kakao id 기준 dedupe
    for hood in cfg["neighborhoods"]:
        aliases = hood.get("query_alias") or [hood["name"]]
        lat, lng = hood["center"]["lat"], hood["center"]["lng"]
        for alias in aliases:
            for category in cfg["categories"]:
                query = f"{alias} {category}"
                try:
                    docs = kakao_search(query, lat, lng, radius, api_key)
                except requests.HTTPError as e:
                    print(f"  [skip] '{query}' 실패: {e}")
                    continue
                for d in docs:
                    pid = d["id"]
                    if pid in raw:
                        continue
                    raw[pid] = {
                        "external_id": f"kakao:{pid}",
                        "source": "kakao",
                        "name": d["place_name"],
                        "city": cfg["city"],
                        "neighborhood": hood["name"],
                        "neighborhood_moods": hood.get("moods", []),
                        "category": category,
                        "category_raw": d.get("category_name", ""),
                        "address": d.get("road_address_name") or d.get("address_name"),
                        "lat": float(d["y"]),
                        "lng": float(d["x"]),
                        "phone": d.get("phone"),
                        "url": d.get("place_url"),
                    }
                time.sleep(0.1)
        print(f"[collect] {hood['name']}: 누적 {len(raw)}곳")

    places = list(raw.values())

    if google_key:
        print(f"[collect] Google 보강 시작 ({len(places)}곳)…")
        for i, p in enumerate(places):
            p.update(google_enrich(p["name"], p["lat"], p["lng"], google_key))
            if (i + 1) % 25 == 0:
                print(f"  보강 {i + 1}/{len(places)}")
            time.sleep(0.1)

    out = stage_file(cfg, "collected")
    out.write_text(json.dumps(places, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[collect] 완료 — {len(places)}곳 → {out}")
    return places
