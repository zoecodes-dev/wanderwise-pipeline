#!/usr/bin/env python3
"""범용 embed + load — 생성/보강 끝난 파일을 임베딩 후 Supabase 적재(pending).

표준 파이프라인 산출물(seoul_*.json)을 건드리지 않고 임의 입력 파일을 처리.
embed 결과는 <in>.embedded.json 으로 캐시 → 재실행 시 재사용.

사용: PYTHONPATH=. python scripts/embed_load.py <in.json> [--embed-only]
"""
import json
import sys
from pathlib import Path

from pipeline.config import load_config
from pipeline import embed as E
from pipeline import load as L

inp = Path(sys.argv[1])
emb_path = inp.with_suffix(".embedded.json")
cfg = load_config("config/seoul.yaml")
places = json.loads(inp.read_text(encoding="utf-8"))
print(f"[embed_load] 입력 {len(places)}곳 ({inp.name})")

# 1) embed (재개)
if emb_path.exists():
    cached = json.loads(emb_path.read_text(encoding="utf-8"))
    if len(cached) == len(places) and all("embedding" in p for p in cached):
        places = cached
        print(f"[embed] 캐시 재사용 {len(places)}곳")
    else:
        emb_path.unlink()
if not emb_path.exists():
    e = cfg["embedding"]
    for i in range(0, len(places), 64):
        chunk = places[i:i + 64]
        texts = E._texts(chunk)
        vecs = (E._voyage(texts, e["model"]) if e["provider"] == "voyage"
                else E._openai(texts, e["model"], e["dimensions"]))
        for p, v in zip(chunk, vecs):
            p["embedding"] = v
        print(f"[embed] {min(i + 64, len(places))}/{len(places)}")
    emb_path.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    print(f"[embed] 완료 → {emb_path.name}")

if "--embed-only" in sys.argv:
    sys.exit(0)

# 2) load (upsert pending)
sb = L.client()
rows = []
for p in places:
    row = {k: p.get(k) for k in L.COLUMNS}
    row["status"] = "pending"
    if row.get("opening_hours") is not None:
        row["opening_hours"] = json.dumps(row["opening_hours"], ensure_ascii=False)
    rows.append(row)
for i in range(0, len(rows), 100):
    sb.table("places").upsert(rows[i:i + 100], on_conflict="external_id").execute()
    print(f"[load] {min(i + 100, len(rows))}/{len(rows)} upsert")
print(f"[load] 완료 — {len(rows)}곳 pending 적재")
