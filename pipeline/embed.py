"""Stage 4 — 임베딩.

description + moods + 동네 컨텍스트를 합친 텍스트로 임베딩 생성.
provider: openai(text-embedding-3-small, 1536) | voyage(voyage-3 등)
"""
import json

import requests

from .config import env, stage_file


def _texts(places: list[dict]) -> list[str]:
    return [
        f"{p['neighborhood']} {p['category']}. {p['description']} "
        f"분위기: {', '.join(p['moods'])}"
        for p in places
    ]


def _openai(texts: list[str], model: str, dims: int) -> list[list[float]]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
        json={"input": texts, "model": model, "dimensions": dims},
        timeout=60,
    )
    resp.raise_for_status()
    return [d["embedding"] for d in resp.json()["data"]]


def _voyage(texts: list[str], model: str) -> list[list[float]]:
    resp = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {env('VOYAGE_API_KEY')}"},
        json={"input": texts, "model": model, "input_type": "document"},
        timeout=60,
    )
    resp.raise_for_status()
    return [d["embedding"] for d in resp.json()["data"]]


def run(cfg: dict, places: list[dict]) -> list[dict]:
    emb = cfg["embedding"]
    for i in range(0, len(places), 64):
        chunk = places[i:i + 64]
        texts = _texts(chunk)
        if emb["provider"] == "voyage":
            vectors = _voyage(texts, emb["model"])
        else:
            vectors = _openai(texts, emb["model"], emb["dimensions"])
        for p, v in zip(chunk, vectors):
            p["embedding"] = v
        print(f"[embed] {min(i + 64, len(places))}/{len(places)}")

    stage_file(cfg, "embedded").write_text(
        json.dumps(places, ensure_ascii=False), encoding="utf-8")
    print(f"[embed] 완료 — {len(places)}곳")
    return places
