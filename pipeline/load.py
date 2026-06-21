"""Stage 5 — Supabase 적재.

external_id 기준 upsert, status='pending'으로 들어가서 검수 후 approved로 전환.
location(geography) 컬럼은 DB 트리거가 lat/lng로부터 자동 생성 (schema.sql 참고).
"""
import json

from supabase import create_client

from .config import env, stage_file

COLUMNS = [
    "external_id", "source", "name", "display_name", "city", "neighborhood",
    "category", "address", "lat", "lng", "phone", "url", "rating", "review_count",
    "price_level", "opening_hours", "hint", "description", "reveal_text",
    "moods", "embedding",
]


def client():
    return create_client(env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY"))


def existing_ids(city: str) -> set[str]:
    sb = client()
    ids, page = set(), 0
    while True:
        rows = (sb.table("places").select("external_id").eq("city", city)
                .range(page * 1000, page * 1000 + 999).execute().data)
        ids.update(r["external_id"] for r in rows if r["external_id"])
        if len(rows) < 1000:
            return ids
        page += 1


def run(cfg: dict, places: list[dict]) -> None:
    sb = client()
    rows = []
    for p in places:
        row = {k: p.get(k) for k in COLUMNS}
        row["status"] = "pending"
        if row.get("opening_hours") is not None:
            row["opening_hours"] = json.dumps(row["opening_hours"], ensure_ascii=False)
        rows.append(row)

    for i in range(0, len(rows), 100):
        sb.table("places").upsert(rows[i:i + 100], on_conflict="external_id").execute()
        print(f"[load] {min(i + 100, len(rows))}/{len(rows)} upsert")

    stage_file(cfg, "loaded").write_text(
        json.dumps([r["external_id"] for r in rows], ensure_ascii=False), encoding="utf-8")
    print(f"[load] 완료 — {len(rows)}곳 pending 상태로 적재. 검수 후 approved로 전환하세요.")
