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
import math
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

구성한 동선은 반드시 emit_itinerary 도구를 호출해 반환하세요. 도구 인자 형식을 그대로 따르고, place_id는 후보 목록에 있는 값만 사용하세요. {stops}개를 고르세요."""

# structured output 강제 — 텍스트 JSON 파싱 대신 tool use로 스키마를 강제해 파싱 깨짐을 원천 차단.
ITINERARY_TOOL = {
    "name": "emit_itinerary",
    "description": "구성한 하루 동선을 반환한다. place_id는 후보 목록의 값만 사용.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "동선 전체를 한 문장으로"},
            "stops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "order": {"type": "integer"},
                        "place_id": {"type": "string", "description": "후보 목록의 place_id"},
                        "arrive_time": {"type": "string", "description": "HH:MM"},
                        "stay_minutes": {"type": "integer"},
                        "transport_from_prev": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string",
                                         "enum": ["walk", "transit", "taxi", "start"]},
                                "minutes": {"type": "integer"},
                            },
                            "required": ["mode", "minutes"],
                        },
                        "direction": {"type": "string",
                                      "description": "이름을 노출하지 않는 러프한 방향 문구"},
                    },
                    "required": ["order", "place_id", "arrive_time",
                                 "stay_minutes", "transport_from_prev", "direction"],
                },
            },
        },
        "required": ["summary", "stops"],
    },
}


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


def _extract_plan(resp, valid_ids: set[str]) -> dict | None:
    """tool_use 블록에서 동선을 꺼낸다. 스키마는 API가 강제하므로 환각 place_id만 검증."""
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_itinerary":
            data = block.input
            stops = data.get("stops")
            if not isinstance(stops, list) or not stops:
                return None
            # 후보에 실재하는 place_id가 하나도 없으면 환각으로 보고 무효
            if not any(s.get("place_id") in valid_ids for s in stops):
                return None
            return data
    return None


def _plan_with_claude(cfg: dict, mood: str, candidates: list[dict],
                      stops: int, start_time: str) -> dict | None:
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    model = cfg["generation"]["model"]
    system = SYSTEM.format(stops=stops)
    user = _build_prompt(mood, candidates, stops, start_time)
    valid_ids = {c["id"] for c in candidates}

    for attempt in (1, 2):  # tool use로 JSON은 강제되지만 API 오류는 1회 재시도
        try:
            resp = client.messages.create(
                model=model, max_tokens=2048, system=system,
                tools=[ITINERARY_TOOL],
                tool_choice={"type": "tool", "name": "emit_itinerary"},
                messages=[{"role": "user", "content": user}],
            )
            plan = _extract_plan(resp, valid_ids)
            if plan:
                return plan
            print(f"  [itinerary] attempt {attempt}: 도구 출력에 유효한 동선 없음")
        except Exception as e:
            print(f"  [itinerary] attempt {attempt} 오류: {e}")
    return None


# ---------- 결정적 플래너 (요청당 LLM 0) ----------
# 감성 콘텐츠(힌트·reveal_text)는 DB에 precompute돼 있으므로, 요청 시엔
# 선택·순서·시간·이동수단·방향만 코드로 결정한다 → 비용 0, 즉시 응답.

_MEAL_CATEGORIES = {"노포", "베이커리", "시장"}  # 끼니 되는 곳 (점심 슬롯 우선 배치)
_STAY_MINUTES = {"전망": 90, "공방": 90, "갤러리": 75, "노포": 75, "독립서점": 60,
                 "바": 60, "공원": 60, "시장": 60, "베이커리": 45, "카페": 60}
_COMPASS_KO = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"]


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371000.0
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _bearing_ko(a: tuple[float, float], b: tuple[float, float]) -> str:
    """a→b 방위를 8방위 한글로."""
    lat1, _ = map(math.radians, a)
    lat2, _ = map(math.radians, b)
    dlng = math.radians(b[1] - a[1])
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    deg = (math.degrees(math.atan2(x, y)) + 360) % 360
    return _COMPASS_KO[int((deg + 22.5) // 45) % 8]


def _transport(dist_m: float, first: bool) -> dict:
    """직전 장소와의 거리로 이동수단·분 추정 (실시간 경로 아님)."""
    if first:
        return {"mode": "start", "minutes": 0}
    if dist_m <= 1000:
        return {"mode": "walk", "minutes": max(1, round(dist_m / 67))}       # 도보 ~4km/h
    if dist_m <= 4000:
        return {"mode": "transit", "minutes": max(5, round(dist_m / 300))}   # 대중교통 대략
    return {"mode": "taxi", "minutes": max(5, round(dist_m / 500))}


def _to_min(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _select_diverse(candidates: list[dict], stops: int) -> list[dict]:
    """유사도 순서 유지하되 카테고리 안 겹치게 우선 선택, 모자라면 상위로 채움."""
    chosen, used_cat, used_id = [], set(), set()
    for c in candidates:
        if len(chosen) >= stops:
            break
        if c["category"] not in used_cat:
            chosen.append(c)
            used_cat.add(c["category"])
            used_id.add(c["id"])
    for c in candidates:
        if len(chosen) >= stops:
            break
        if c["id"] not in used_id:
            chosen.append(c)
            used_id.add(c["id"])
    return chosen


def _order_nearest(places: list[dict], lat: float, lng: float) -> list[dict]:
    """시작 위치에서 최근접 이웃으로 정렬 — 지그재그 최소화."""
    remaining, ordered, cur = list(places), [], (lat, lng)
    while remaining:
        nxt = min(remaining, key=lambda c: _haversine(cur, (c["lat"], c["lng"])))
        ordered.append(nxt)
        remaining.remove(nxt)
        cur = (nxt["lat"], nxt["lng"])
    return ordered


def _bring_meal_to_lunch(ordered: list[dict], start_time: str) -> list[dict]:
    """점심대(12:30 근처) 슬롯에 끼니 카테고리가 오도록 best-effort 스왑."""
    base = _to_min(start_time)
    if base is None or len(ordered) < 2:
        return ordered
    est = [base + i * 90 for i in range(len(ordered))]  # 한 곳당 대략 90분(체류+이동)
    slot = min(range(len(ordered)), key=lambda i: abs(est[i] - (12 * 60 + 30)))
    if ordered[slot]["category"] in _MEAL_CATEGORIES:
        return ordered
    meal_idx = next((i for i, c in enumerate(ordered) if c["category"] in _MEAL_CATEGORIES), None)
    if meal_idx is not None:
        ordered[slot], ordered[meal_idx] = ordered[meal_idx], ordered[slot]
    return ordered


def _summary(ordered: list[dict]) -> str:
    if not ordered:
        return "오늘의 동선."
    first, last = ordered[0], ordered[-1]
    if len(ordered) == 1:
        return f"{first['neighborhood']} {first['category']} 한 곳에 머무는 하루."
    return (f"{first['neighborhood']} {first['category']}에서 시작해 "
            f"{last['neighborhood']} {last['category']}로 마무리하는 {len(ordered)}곳의 하루 동선.")


def _plan_deterministic(candidates: list[dict], stops: int, start_time: str,
                        lat: float, lng: float) -> dict:
    """선택(다양성)→순서(최근접)→점심슬롯→시간·이동·방향 배치. LLM 없음."""
    ordered = _bring_meal_to_lunch(
        _order_nearest(_select_diverse(candidates, stops), lat, lng), start_time)

    out, depart, prev = [], start_time, (lat, lng)
    for i, c in enumerate(ordered):
        first = i == 0
        cur = (c["lat"], c["lng"])
        dist = _haversine(prev, cur)
        tp = _transport(dist, first)
        # 방향 안내용 이동 분 — 첫 구간은 transport가 start/0이라 거리로 추정
        leg_min = tp["minutes"] if not first else max(1, round(dist / (67 if dist <= 1000 else 300)))
        arrive = depart if first else _add_minutes(depart, tp["minutes"])
        stay = _STAY_MINUTES.get(c["category"], 60)
        out.append({
            "order": i + 1,
            "place_id": c["id"],
            "arrive_time": arrive,
            "stay_minutes": stay,
            "transport_from_prev": tp,
            "direction": f"{_bearing_ko(prev, cur)}쪽으로 약 {leg_min}분",
        })
        depart = _add_minutes(arrive, stay) if arrive else depart
        prev = cur
    return {"summary": _summary(ordered), "stops": out}


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

    # 기본은 결정적 플래너(요청당 LLM 0). config에서 itinerary.planner: llm 으로 두면 Claude 사용.
    planner = (cfg.get("itinerary") or {}).get("planner", "deterministic")
    fellback = False
    if planner == "llm":
        plan = _plan_with_claude(cfg, mood, candidates, stops, start_time)
        if plan is None:  # LLM 실패 시 결정적으로 폴백
            plan = _plan_deterministic(candidates, stops, start_time, lat, lng)
            fellback = True
    else:
        plan = _plan_deterministic(candidates, stops, start_time, lat, lng)

    resp = _join(plan, candidates)
    resp["mood"] = mood
    resp["fallback"] = fellback
    resp["candidate_count"] = len(candidates)
    return resp
