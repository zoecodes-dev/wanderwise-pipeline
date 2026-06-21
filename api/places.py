"""2단계 — Supabase 클라이언트 + match_places RPC 래퍼.

무드 문장과 좌표를 받아 후보 장소 리스트를 반환한다.
match_count = max(20, stops*4)  (api_plan.md 결정사항 ③)

주의: match_places는 status='approved' 인 장소만 반환한다(schema.sql).
적재 직후 pending 상태에서는 후보가 0개로 나오는 게 정상.
"""
from supabase import create_client

from pipeline.config import env

from .embedding import embed_query


def client():
    """load.py와 동일한 service key 클라이언트."""
    return create_client(env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY"))


def find_candidates(
    cfg: dict,
    mood: str,
    lat: float,
    lng: float,
    *,
    city: str = "seoul",
    max_distance_m: float = 5000,
    stops: int = 4,
) -> list[dict]:
    """무드 → 임베딩 → match_places 호출 → 후보 리스트(유사도 내림차순)."""
    query_embedding = embed_query(cfg, mood)
    match_count = max(20, stops * 4)

    sb = client()
    resp = sb.rpc(
        "match_places",
        {
            "query_embedding": query_embedding,
            "user_lat": lat,
            "user_lng": lng,
            "max_distance_m": max_distance_m,
            "match_count": match_count,
            "target_city": city,
        },
    ).execute()
    # match_places는 이미 embedding <=> query 순(=유사도 내림차순)으로 정렬해 반환.
    return resp.data or []


def fetch_display_names(ids: list[str]) -> dict[str, str]:
    """선택된 place_id의 display_name 보조 조회.

    match_places 리턴 컬럼에는 display_name이 없어서(schema.sql) reveal 블록용으로 따로 가져온다.
    DB 함수를 고치지 않기 위한 우회 — 읽기 전용 select.
    """
    if not ids:
        return {}
    sb = client()
    rows = sb.table("places").select("id, display_name").in_("id", ids).execute().data
    return {r["id"]: r["display_name"] for r in rows if r.get("display_name")}
