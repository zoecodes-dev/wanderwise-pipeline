"""프론트 고정 무드 라벨의 쿼리 임베딩을 미리 계산해 api/mood_embeddings.json 으로 저장.

요청 때 OpenAI 임베딩 호출을 없애기 위함(비용 0). 무드 라벨을 바꾸면 다시 실행할 것.
생성된 벡터는 config/seoul.yaml 의 embedding 설정(provider·model·dimensions)과 일치한다.

실행: python scripts/precompute_mood_embeddings.py   (OPENAI_API_KEY 필요)
"""
import json
from pathlib import Path

from pipeline.config import load_config
import api.embedding as embedding

# 프론트 app/mood.tsx 의 label들과 정확히 일치시킬 것
MOODS = ["로맨틱", "평화로운", "모험적인", "창의적인", "호기심",
         "현지인처럼", "여유롭게", "활기찬", "사교적인", "사색적인"]


def main():
    cfg = load_config("config/seoul.yaml")
    embedding._PRECOMPUTED = {}  # 재생성 시 캐시 무시하고 라이브로 다시 계산
    out = {}
    for m in MOODS:
        vec = embedding.embed_query(cfg, m)
        out[m] = vec
        print(f"  {m}: dim={len(vec)}")
    path = Path("api/mood_embeddings.json")
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"저장: {path} ({len(out)}개 무드)")


if __name__ == "__main__":
    main()
