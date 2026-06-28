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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from pipeline.config import load_config
from .itinerary import build_itinerary


def _client_ip(request: Request) -> str:
    """레이트 리밋 키 — Railway 프록시 뒤라 X-Forwarded-For의 첫 IP(원 클라이언트)를 쓴다."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


# 공개 엔드포인트라 IP당 호출 제한 — 무단 호출·과금 폭주 방지 (동선 생성은 LLM/임베딩 비용 큼)
limiter = Limiter(key_func=_client_ip)

app = FastAPI(title="WanderWise Itinerary API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
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
@limiter.limit("10/minute;100/day")
def create_itinerary(request: Request, req: ItineraryRequest):
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
