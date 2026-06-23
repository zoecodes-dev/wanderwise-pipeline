"""5단계 — FastAPI 라우터.

POST /itinerary: 무드+좌표 → build_itinerary(임베딩→match_places→Claude 동선→join) → 응답.
응답은 api_plan.md 1번 형태(stops + reveal 분리). 동선 생성 로직은 api/itinerary.py 그대로 사용.

엣지케이스(api_plan.md 4번):
- 빈 무드 → 400
- 후보 0개 → 404
- 후보 < stops → 자동 축소 후 adjusted=true

실행: uvicorn api.app:app --port 8000
"""
import textwrap

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from pipeline.config import load_config
from .itinerary import build_itinerary

app = FastAPI(title="WanderWise Itinerary API")
_CFG = load_config("config/seoul.yaml")  # 프로세스 기동 시 1회 로드

_MOOD_MAX = 500  # 초장문 무드는 잘라서 임베딩 (토큰 보호)


class ItineraryRequest(BaseModel):
    mood: str
    lat: float
    lng: float
    city: str = "seoul"
    max_distance_m: float = 5000
    stops: int = Field(default=4, ge=3, le=6)  # 범위 벗어나면 422
    start_time: str = "10:00"


@app.get("/")
def read_root():
    ascii_art = """
       .  * .          .      *
  * .         * .
    .   ✨  WanderWise  ✨   .
        .          .       .
  _  .  .       * .       .
 / \\ / \\    .      * .
/   V   \\       .        .
\\_______/   * .
  | _ |  _______   .     .
  | _ | /       \\      *
  | _ |/  👕 👔  \\  .
==========================
  ~~~~ ~~~~ ~~~~ ~~~~ ~~~~
    WanderWise Hills 🌙
    """
    return PlainTextResponse(textwrap.dedent(ascii_art))


@app.post("/itinerary")
def create_itinerary(req: ItineraryRequest):
    mood = (req.mood or "").strip()
    if not mood:
        raise HTTPException(status_code=400, detail="무드 문장이 비어 있습니다.")
    mood = mood[:_MOOD_MAX]

    result = build_itinerary(
        _CFG, mood, req.lat, req.lng,
        city=req.city, max_distance_m=req.max_distance_m,
        stops=req.stops, start_time=req.start_time,
    )

    if result.get("error") == "no_candidates":
        raise HTTPException(status_code=404, detail="이 근처엔 아직 추천이 없어요.")

    # 후보가 요청 stops보다 적으면 build_itinerary가 자동 축소함 → adjusted로 표시
    adjusted = result["candidate_count"] < req.stops

    return {
        "mood": result["mood"],
        "summary": result["summary"],
        "adjusted": adjusted,
        "fallback": result["fallback"],
        "candidate_count": result["candidate_count"],
        "stops": result["stops"],
    }
