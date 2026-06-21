"""1단계 — 무드 텍스트 → 쿼리 임베딩.

pipeline/embed.py의 provider·model·dimensions를 그대로 재사용한다.
다른 점은 입력이 사용자 무드 문장 하나라는 것뿐:
- OpenAI: 문서 임베딩과 동일 호출.
- Voyage: input_type을 "query"로 (문서는 "document"였음 — 비대칭 임베딩).

벡터 공간이 적재된 장소 임베딩과 일치해야 match_places의 유사도가 의미를 가진다.
"""
import json
from pathlib import Path

import requests

from pipeline.config import env

# 고정 무드 라벨(프론트 10개)의 임베딩은 미리 계산해 둔다 → 요청 때 OpenAI 호출 0.
# scripts/precompute_mood_embeddings.py 로 생성. 자유 입력 무드는 라이브 임베딩으로 폴백.
_PRECOMPUTED_PATH = Path(__file__).resolve().parent / "mood_embeddings.json"
try:
    _PRECOMPUTED: dict[str, list[float]] = json.loads(_PRECOMPUTED_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    _PRECOMPUTED = {}


def _openai(text: str, model: str, dims: int) -> list[float]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
        json={"input": [text], "model": model, "dimensions": dims},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def _voyage(text: str, model: str) -> list[float]:
    resp = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {env('VOYAGE_API_KEY')}"},
        json={"input": [text], "model": model, "input_type": "query"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def embed_query(cfg: dict, mood: str) -> list[float]:
    """무드 문장 하나를 config의 임베딩 설정대로 벡터화."""
    mood = (mood or "").strip()
    if not mood:
        raise ValueError("무드 문장이 비어 있습니다.")
    cached = _PRECOMPUTED.get(mood)  # 고정 라벨이면 호출 없이 즉시
    if cached is not None:
        return cached
    emb = cfg["embedding"]
    if emb["provider"] == "voyage":
        return _voyage(mood, emb["model"])
    return _openai(mood, emb["model"], emb["dimensions"])
