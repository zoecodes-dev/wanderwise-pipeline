"""3~4단계 — Claude 동선 구성 + DB join.

3단계: find_candidates로 받은 후보를 Claude에 넘겨 stops개를 골라
       하루 동선의 순서·체류시간·이동수단을 짜게 한다.
       Claude는 place_id 선별·순서·시간만 결정 — 이름/묘사/힌트/좌표는 만들지 않는다.
4단계: Claude가 고른 place_id를 키로, DB 후보에서 display_name·reveal_text·hint·
       category·좌표를 가져와 응답에 합친다. reveal 정보는 별도 reveal 블록으로 분리.

JSON 파싱 실패 시 1회 재시도 → 폴백(유사도 상위 stops개를 거리순으로).
FastAPI 라우터(5단계)는 아직 만들지 않음 — 이 모듈은 순수 함수로 동선을 반환한다.
"""
import json
from datetime import datetime, timedelta

import anthropic

from pipeline.config import env

from .places import find_candidates, fetch_display_names

_MODE_KO = {"walk": "도보", "transit": "대중교통", "taxi": "택시", "start": "출발"}

SYSTEM = """당신은 여행 앱 WanderWise의 동선 설계자입니다. WanderWise는 목적지 이름을 도착 전까지 숨기고, 방향·시적 힌트·하루 동선으로 여행자를 안내합니다.

후보 장소 목록(place_id와 분위기·카테고리·위치 정보)이 주어집니다. 이 중에서 {stops}개를 골라 하루 동선의 순서·체류시간·이동수단을 설계하세요.

당신이 결정하는 것: 어떤 place_id를 고를지, 방문 순서, 도착 시각, 체류 시간(분), 직전 장소에서의 이동수단.
당신이 만들지 않는 것: 장소 이름·display_name·묘사·힌트·카테고리·좌표. 이 정보는 서버가 place_id로 채웁니다. 절대 새로 지어내지 마세요.

제약:
- candidates에 있는 place_id만 사용. 목록에 없는 id를 만들어내지 말 것.
- 지리적으로 효율적인 동선. lat/lng와 distance_m을 보고 너무 왔다갔다 하지 않게 묶을 것.
- 점심 시간대(12~13시 무렵)에 음식 계열(노포·베이커리 등 먹는 곳)이 오도록 가능하면 배치.
- 카테고리가 한쪽으로 쏠리지 않게 다양성 고려(전부 카페로 채우지 말 것).
- 이동수단(transport_from_prev.mode)은 직전 장소와의 거리로 추정: 약 1km 이내면 "walk", 그 이상이면 "transit" 또는 "taxi". 좌표로 추정한 대략값이며 실시간 경로가 아님.
- direction: 장소 이름을 노출하지 않는 러프한 방향 문구(예: "지하철역에서 북쪽으로 5분"). 정확하지 않아도 됨.
- candidates의 rank는 무드 유사도의 '상대 순위'입니다(1이 가장 유사). 유사도 점수의 절대값으로 컷하지 말고, 순위와 동선 적합성으로 고르세요.

반드시 아래 JSON만 반환하세요. 마크다운 코드펜스·설명·인사 금지.
{{"summary": "동선 전체를 한 문장으로", "stops": [{{"order": 1, "place_id": "...", "arrive_time": "10:00", "stay_minutes": 60, "transport_from_prev": {{"mode": "walk", "minutes": 0}}, "direction": "..."}}]}}"""


def _build_prompt(mood: str, candidates: list[dict], stops: int, start_time: str) -> str:
    items = [
        {
            "place_id": c["id"],
            "rank": i,
            "neighborhood": c["neighborhood"],
            "category": c["category"],
            "hint": c["hint"],
            "description": c["description"],
            "moods": c["moods"],
            "lat": c["lat"],
            "lng": c["lng"],
            "distance_m": round(c["distance_m"]),
        }
        for i, c in enumerate(candidates, 1)
    ]
    return (
        f"무드: {mood}\n시작 시각: {start_time}\n고를 장소 수: {stops}\n\n"
        f"후보 (rank=무드 유사도 상대순위, 1이 가장 유사. 유사도 절대값 컷 금지):\n"
        f"{json.dumps(items, ensure_ascii=False, indent=1)}"
    )


def _parse(text: str, valid_ids: set[str]) -> dict | None:
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(clean)
    stops = data.get("stops")
    if not isinstance(stops, list) or not stops:
        return None
    # 후보에 실재하는 place_id가 하나도 없으면 환각으로 보고 무효
    if not any(s.get("place_id") in valid_ids for s in stops):
        return None
    return data


def _plan_with_claude(cfg: dict, mood: str, candidates: list[dict],
                      stops: int, start_time: str) -> dict | None:
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    model = cfg["generation"]["model"]
    system = SYSTEM.format(stops=stops)
    user = _build_prompt(mood, candidates, stops, start_time)
    valid_ids = {c["id"] for c in candidates}

    for attempt in (1, 2):  # 파싱 실패 시 1회 재시도
        try:
            resp = client.messages.create(
                model=model, max_tokens=2048, system=system,
                messages=[{"role": "user", "content": user}],
            )
            plan = _parse(resp.content[0].text, valid_ids)
            if plan:
                return plan
            print(f"  [itinerary] attempt {attempt}: 유효한 동선 JSON 아님")
        except Exception as e:
            print(f"  [itinerary] attempt {attempt} 오류: {e}")
    return None


def _fallback(candidates: list[dict], stops: int, start_time: str) -> dict:
    """LLM 없는 폴백 — 유사도 상위 stops개를 거리 가까운 순으로 잇는다."""
    chosen = sorted(candidates[:stops], key=lambda c: c["distance_m"])
    out, t = [], start_time
    for i, c in enumerate(chosen, 1):
        first = i == 1
        out.append({
            "order": i,
            "place_id": c["id"],
            "arrive_time": t,
            "stay_minutes": 60,
            "transport_from_prev": {"mode": "start" if first else "walk",
                                    "minutes": 0 if first else 10},
            "direction": "다음 장소로 이동",
        })
        t = _add_minutes(t, 60 + (0 if first else 10))
    return {
        "summary": "(폴백) 유사도 상위 장소를 가까운 순서로 연결한 기본 동선.",
        "stops": out,
    }


def _add_minutes(hhmm: str, mins: int) -> str | None:
    try:
        return (datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=mins)).strftime("%H:%M")
    except (ValueError, TypeError):
        return None


def _join(plan: dict, candidates: list[dict]) -> dict:
    """4단계 — Claude의 place_id를 키로 DB 후보 정보를 합친다. reveal은 별도 블록."""
    by_id = {c["id"]: c for c in candidates}
    chosen_ids = [s.get("place_id") for s in plan.get("stops", []) if s.get("place_id") in by_id]
    display_names = fetch_display_names(list(dict.fromkeys(chosen_ids)))

    out, seen = [], set()
    for s in plan.get("stops", []):
        pid = s.get("place_id")
        c = by_id.get(pid)
        if not c or pid in seen:  # 환각 place_id / 중복 드롭
            continue
        seen.add(pid)

        arrive = s.get("arrive_time")
        stay = s.get("stay_minutes") or 60
        tp = s.get("transport_from_prev") or {}
        mode = tp.get("mode", "walk")
        mins = tp.get("minutes", 0)
        first = not out
        out.append({
            "order": len(out) + 1,
            "place_id": pid,
            # --- 도착 전까지 노출 (이름 숨김) ---
            "hint": c["hint"],
            "category": c["category"],
            "neighborhood": c["neighborhood"],
            "direction": s.get("direction"),
            "arrive_time": arrive,
            "depart_time": _add_minutes(arrive, stay) if arrive else None,
            "stay_minutes": stay,
            "transport": {
                "mode": mode,
                "minutes": mins,
                "from_prev": "출발 지점" if first
                             else f"이전 장소에서 {_MODE_KO.get(mode, mode)} {mins}분",
            },
            # --- 도착 시점에만 클라이언트가 공개 ---
            "reveal": {
                "display_name": display_names.get(pid) or c.get("name"),
                "reveal_text": c["reveal_text"],
                "lat": c["lat"],
                "lng": c["lng"],
            },
        })
    return {"summary": plan.get("summary"), "stops": out}


def build_itinerary(cfg: dict, mood: str, lat: float, lng: float, *,
                    city: str = "seoul", max_distance_m: float = 5000,
                    stops: int = 4, start_time: str = "10:00") -> dict:
    """무드 → 후보 추출 → Claude 동선 → join → 응답(reveal 분리)."""
    candidates = find_candidates(cfg, mood, lat, lng, city=city,
                                 max_distance_m=max_distance_m, stops=stops)
    if not candidates:
        return {"mood": mood, "summary": None, "stops": [],
                "error": "no_candidates", "fallback": False}

    stops = min(stops, len(candidates))  # 후보가 적으면 자동 축소
    plan = _plan_with_claude(cfg, mood, candidates, stops, start_time)
    fallback = plan is None
    if fallback:
        plan = _fallback(candidates, stops, start_time)

    resp = _join(plan, candidates)
    resp["mood"] = mood
    resp["fallback"] = fallback
    resp["candidate_count"] = len(candidates)
    return resp
