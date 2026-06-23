# 일정 생성 API 설계 (2일차)

## 목표
무드 입력 → `match_places`로 후보 추출 → Claude가 하루 동선 구성(시간·거리·교통 고려) → reveal 정보 포함해 반환.
포트폴리오 데모용 한 줄기: "무드 선택 → 동선 생성(이름 숨김) → 도착 시 reveal"이 영상으로 찍힐 만큼 매끄럽게.

스택: FastAPI on Railway, Supabase(`match_places` RPC), Claude API, OpenAI/Voyage 임베딩(파이프라인과 동일 provider).

---

## 1. 엔드포인트 시그니처

### `POST /itinerary`

요청:
```jsonc
{
  "mood": "비 오는 날 혼자 사색하기 좋은",   // 자유 텍스트 무드 (필수)
  "lat": 37.5447,                          // 시작 위치 위도 (필수)
  "lng": 127.0557,                         // 시작 위치 경도 (필수)
  "city": "seoul",                         // 기본 "seoul"
  "max_distance_m": 5000,                  // match_places로 전달, 기본 5000
  "stops": 4,                              // 동선에 넣을 장소 수 (기본 4, 범위 3~6)
  "start_time": "10:00"                    // 동선 시작 시각 (기본 "10:00")
}
```

응답 (200):
```jsonc
{
  "mood": "비 오는 날 혼자 사색하기 좋은",
  "summary": "성수의 골목을 천천히 도는 반나절...",  // 동선 전체를 한 문장으로
  "stops": [
    {
      "order": 1,
      "place_id": "uuid",
      // --- 도착 전까지 노출 (이름 숨김) ---
      "hint": "낡은 철문 사이로 커피 볶는 냄새가...",
      "category": "cafe",
      "neighborhood": "성수동",
      "direction": "지하철역 2번 출구에서 북쪽으로 300m",   // 이름 대신 방향
      "arrive_time": "10:00",
      "depart_time": "11:00",
      "stay_minutes": 60,
      "transport": { "mode": "walk", "minutes": 8, "from_prev": "이전 장소에서 도보 8분" },
      // --- 도착 시점에만 클라이언트가 공개 ---
      "reveal": {
        "display_name": "대림창고",
        "reveal_text": "낡은 철문 너머, 당신이 찾던 곳은 대림창고였습니다.",
        "lat": 37.5447,
        "lng": 127.0557
      }
    }
    // ...
  ]
}
```

설계 포인트:
- **이름 숨김 보장은 서버 책임.** `hint`/`direction`에는 절대 `name`·`display_name`이 들어가지 않게 하고, 실명/표시명은 `reveal` 블록에만 둔다. (프론트가 도착 판정 전까지 `reveal`을 렌더하지 않으면 됨. 더 엄격히 하려면 별도 `GET /itinerary/{id}/reveal/{order}` 분리도 가능 — 데모 단계에선 한 응답에 담되 키로 분리.)
- `place_id`로 클라이언트가 추후 상세 조회 가능.
- 응답에 `name`(정식 상호) 자체는 **포함하지 않음.** reveal에 쓰는 건 `display_name`뿐.

---

## 2. `match_places` 호출

`schema.sql`의 함수 시그니처:
```
match_places(query_embedding vector(1536), user_lat, user_lng,
             max_distance_m default 5000, match_count default 20,
             target_city default 'seoul')
→ id, name, neighborhood, category, hint, description, reveal_text, moods,
  lat, lng, distance_m, similarity
```

호출 흐름:
1. **무드 텍스트 → 쿼리 임베딩.**
   파이프라인(`pipeline/embed.py`)과 **동일 provider·model·dimensions**로 임베딩해야 벡터 공간이 맞는다.
   - 파이프라인은 장소 임베딩 시 `"{neighborhood} {category}. {description} 분위기: {moods}"` 형태의 *문서* 텍스트를 씀.
   - 쿼리는 사용자 무드 문장을 그대로 임베딩. Voyage 사용 시 `input_type`을 `"query"`로(문서는 `"document"`였음). OpenAI는 구분 없음.
   - 차원은 config의 `embedding.dimensions`(=1536, 스키마의 `vector(1536)`과 일치) 사용.
2. **Supabase RPC 호출.** `supabase.rpc("match_places", {...})`로 위 파라미터 전달.
   - `match_count`는 `stops`보다 넉넉히(예: `max(20, stops*4)`) 받아서 Claude가 동선을 고를 여지를 준다.
   - 함수가 이미 `status='approved' AND city=target_city` + 거리 필터 + 무드 유사도 정렬을 한 쿼리로 처리하므로 추가 필터 불필요.
3. **후보를 Claude 입력용으로 정리.** id, neighborhood, category, hint, description, moods, lat/lng, distance_m, similarity만 추려 전달.

---

## 3. Claude 동선 구성 프롬프트 구조

역할: 후보 N개 중에서 `stops`개를 골라 **하루 동선 순서**를 짜고, 각 구간 이동(시간·교통)과 체류 시간을 채운다. 묘사 텍스트 생성은 파이프라인에서 이미 끝났으니 **여기서는 선별·순서·시간 배치**가 핵심.

### system
- WanderWise 컨셉 설명(이름 숨김, 방향·동선으로 안내).
- 임무: 주어진 후보 장소들로 자연스러운 하루 동선을 구성. **장소 이름을 새로 지어내거나 hint를 변형하지 말 것** — 주어진 필드를 그대로 인용/참조.
- 제약:
  - 후보 목록에 있는 `place_id`만 사용(환각 금지).
  - 동선은 지리적으로 효율적이게(좌표·distance_m 기반으로 너무 왔다갔다 하지 않게).
  - 식사 시간대(점심 12~13시 무렵)에 음식 카테고리가 오도록 가능하면 배치.
  - 카테고리가 한쪽으로 쏠리지 않게 다양성 고려(카페만 4개 X).
  - 이동수단은 거리 기준 휴리스틱: ~1km 도보, 그 이상 대중교통/택시. **정확한 실시간 경로 API는 데모 범위 밖** — 거리로 추정한 대략값임을 전제.

### user (구조화 입력)
```jsonc
{
  "mood": "...",
  "start_time": "10:00",
  "stops": 4,
  "candidates": [
    {"place_id": "uuid", "neighborhood": "...", "category": "cafe",
     "hint": "...", "description": "...", "moods": ["..."],
     "lat": .., "lng": .., "distance_m": 420, "similarity": 0.81}
    // ...N개
  ]
}
```

### 출력 (JSON only, tool/structured output로 강제)
```jsonc
{
  "summary": "...",
  "stops": [
    {"order": 1, "place_id": "uuid", "arrive_time": "10:00",
     "stay_minutes": 60, "transport_from_prev": {"mode": "walk", "minutes": 0},
     "direction": "지하철역에서 북쪽으로 300m"}
  ]
}
```

설계 포인트:
- **Claude는 선별·순서·시간만 결정.** `display_name`/`reveal_text`/`hint`/`category`/좌표는 서버가 DB 결과에서 가져와 응답에 합친다 — Claude 출력의 `place_id`를 키로 join. (LLM이 이름·reveal을 다시 쓰게 하면 환각·실명 누출 위험.)
- `direction`은 좌표만으로 LLM이 만들기 어려움 → **방안 A: 단순 방위/거리만 생성**(이전 stop 좌표 대비 방위 계산은 서버에서 해 LLM엔 힌트만), **방안 B: 데모에선 "○○역 방향" 수준의 러프한 문구**. 데모는 B로 시작, 정교화는 다음 단계.
- 파이프라인 `generate.py`와 동일하게: JSON 파싱 실패 시 1회 재시도, 그래도 실패면 폴백(아래 엣지케이스).
- 모델: config `generation.model`과 동일 계열(최신 Claude) 사용.

---

## 4. 엣지케이스

| 상황 | 처리 |
|------|------|
| **후보 0개** (거리 내 approved 장소 없음) | 422/404로 "이 근처엔 아직 추천이 없어요" 반환. `max_distance_m`를 한 단계 늘려 1회 재시도(예: 5km→10km) 후에도 0이면 실패. |
| **후보 < stops** (예: 요청 4곳, 후보 2곳) | stops를 후보 수로 자동 축소(`min(stops, len(candidates))`)하고 응답에 `adjusted: true` 표시. 0이면 위 케이스. |
| **Claude가 없는 place_id 반환** | join 시 매칭 안 되는 항목 드롭. 남은 수가 너무 적으면 재시도. |
| **Claude가 같은 place_id 중복** | dedup, 부족분은 후보에서 유사도순 보충. |
| **Claude JSON 파싱 실패** | 1회 재시도 → 그래도 실패면 **LLM 없는 폴백**: 유사도 상위 `stops`개를 거리순으로 잇고 균등 체류시간(60분)·도보 가정으로 동선 구성. 데모가 멈추지 않게. |
| **임베딩 API 오류** | 502로 명확히 반환(무드→벡터 없으면 매칭 불가). |
| **무드 텍스트 빈 값/초장문** | 빈 값 400. 초장문은 잘라서 임베딩(토큰 한도). |
| **거리 추정 부정확** | 응답에 "예상 이동시간" 임을 명시하는 필드/문구. 실시간 경로는 범위 밖. |
| **reveal 조기 노출** | reveal 정보를 별도 키로 분리(위 1번). 엄격 모드는 reveal 전용 엔드포인트로 분리 가능. |
| **status≠approved 누출** | `match_places`가 이미 `approved`만 반환하므로 서버에서 추가 필터 불필요(함수에 의존). |

---

## 5. 구현 순서 제안 (코드는 다음 단계)
1. 무드→임베딩 유틸 (파이프라인 embed 로직 재사용, query 모드).
2. Supabase 클라이언트 + `match_places` RPC 래퍼.
3. Claude 동선 호출 (structured output + 재시도 + 폴백).
4. DB 결과 ↔ Claude 출력 join → 응답 조립(이름 숨김/ reveal 분리 보장).
5. FastAPI 라우터 `POST /itinerary` + 엣지케이스 처리.
6. 작은 무드 1~2개로 실제 호출 검증(크레딧·실제 출력 확인) 후 데모.

> 미해결/결정 필요: ① reveal을 한 응답에 담을지 별도 엔드포인트로 뺄지, ② `direction` 생성 방식(서버 방위 계산 vs LLM 러프 문구), ③ `match_count` 배수. 모두 데모용 기본값을 잡아두고 진행 가능.
