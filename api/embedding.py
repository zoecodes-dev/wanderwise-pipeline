"""1단계 — 무드 텍스트 → 쿼리 임베딩.

pipeline/embed.py의 provider·model·dimensions를 그대로 재사용한다.
다른 점은 입력이 사용자 무드 문장 하나라는 것뿐:
- OpenAI: 문서 임베딩과 동일 호출.
- Voyage: input_type을 "query"로 (문서는 "document"였음 — 비대칭 임베딩).

벡터 공간이 적재된 장소 임베딩과 일치해야 match_places의 유사도가 의미를 가진다.
"""
import requests

from pipeline.config import env


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
    emb = cfg["embedding"]
    if emb["provider"] == "voyage":
        return _voyage(mood, emb["model"])
    return _openai(mood, emb["model"], emb["dimensions"])
