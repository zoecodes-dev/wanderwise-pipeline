# WanderWise

> **Designed Serendipity** — 목적지의 *이름*은 도착하는 순간까지 숨긴다. 무드를 고르면 방향·시적 힌트·하루 동선만 받고, 발걸음이 닿는 순간 그곳의 이름이 *공개(reveal)* 된다.

여행을 "검색해서 가는 것"에서 "이끌려 도착하는 것"으로 바꾸는 여행 앱. 이 레포는 그 경험을 떠받치는 **데이터 파이프라인 + 일정 생성 API**다.

```
무드 선택  →  하루 동선 생성 (이름 숨김, 경로·시간은 투명)  →  도착 시 reveal
```

---

## 무엇을 풀었나

장소 추천은 흔하다. 어려운 건 **"이름을 숨긴 채로도 신뢰가 가는 안내"** 를 만드는 것:

- 무드("비 오는 날 혼자 사색하기 좋은")를 **임베딩 유사도**로 장소와 매칭하고,
- 거리·시간·교통을 고려해 **지리적으로 말이 되는 하루 동선**으로 엮고,
- hint/방향엔 이름을 절대 흘리지 않으면서, **reveal 정보는 도착 시점에만** 꺼내 쓰도록 서버가 구조적으로 분리한다.

---

## 데모 — `POST /itinerary`

요청:

```jsonc
{
  "mood": "비 오는 날 혼자 사색하기 좋은",
  "lat": 37.5446, "lng": 127.0559,   // 시작 위치 (성수동)
  "stops": 4
}
```

응답 (발췌) — **도착 전 정보**와 **reveal**이 키로 분리되어 있다:

```jsonc
{
  "mood": "비 오는 날 혼자 사색하기 좋은",
  "summary": "성수동 갤러리에서 시작해 성수동 공원으로 마무리하는 4곳의 하루 동선.",
  "adjusted": false,
  "stops": [
    {
      "order": 1,
      "category": "갤러리", "neighborhood": "성수동",
      "hint": "흰 벽보다 먼저 눈에 들어오는 것은 조명 각도다. 작품과 빛이 함께 설계된 공간, 말수 적은 갤러리.",
      "direction": "남서쪽으로 약 5분",          // ← 이름 없이 방향만
      "arrive_time": "10:00", "depart_time": "11:15", "stay_minutes": 75,
      "transport": { "mode": "start", "minutes": 0, "from_prev": "출발 지점" },
      "reveal": {                                // ← 도착 시점에만 클라이언트가 렌더
        "display_name": "CDA",
        "reveal_text": "빛과 작품이 대화하는 곳, CDA에 도착했습니다.",
        "lat": 37.5439, "lng": 127.0572
      }
    }
    // ... 점심대(12:30 근처) 슬롯엔 끼니 카테고리(노포·베이커리·시장)가 오도록 배치
  ]
}
```

**엣지케이스**도 라우터에서 처리: 빈 무드 `400`, 반경 내 후보 0개 `404`, 후보 < 요청 stops면 자동 축소 후 `"adjusted": true`.

---

## 어떻게 동작하나

```
[ 서빙 ]  POST /itinerary
  무드 텍스트 ──임베딩──▶ match_places() ──후보 N──▶ 플래너 ──join──▶ 응답(reveal 분리)
                          │                          │
              PostGIS×pgvector 한 쿼리        기본: 결정적(LLM 0) / 옵션: Claude tool use

[ 파이프라인 ]  config/seoul.yaml 의 동네×카테고리 정의로 장소 DB를 채운다
  [1] collect   카카오 로컬 수집 (+선택: Google 평점·영업시간 보강)
  [2] filter    프랜차이즈·체인 자동 탐지, 평점 구간, 동네×카테고리 균형 선별
  [3] generate  Claude — 힌트·display_name·reveal 문장·무드 태그 (tool 검증 + 재시도)
  [4] embed     OpenAI/Voyage 임베딩 (pgvector)
  [5] load      Supabase upsert, status='pending' → 검수 후 approved
```

### 핵심 쿼리 — 분위기와 거리를 한 번에

`match_places()`(schema.sql)가 **pgvector 유사도 + PostGIS 거리 필터**를 단일 SQL로 처리한다. 후처리 없이 "이 근처에서 이 무드에 맞는 곳"이 정렬되어 나온다:

```sql
order by p.embedding <=> query_embedding        -- 무드 유사도
where  st_dwithin(p.location, :user, :radius)    -- 거리
  and  p.status = 'approved' and p.city = :city  -- 검수된 것만
```

---

## 기술적 결정 (그리고 이유)

- **이름 숨김은 서버의 책임.** hint·direction엔 `display_name`이 들어가지 못하게 하고, 실명은 `reveal` 블록에만 둔다. 프론트가 도착 판정 전까지 reveal을 렌더하지 않으면 컨셉이 깨지지 않는다. LLM이 이름을 *다시 쓰지* 못하도록, 동선 플래너는 `place_id`만 다루고 이름·좌표·reveal은 DB 결과에서 join한다.

- **요청당 LLM 호출 0이 기본값.** 감성 텍스트(힌트·reveal)는 파이프라인에서 미리 생성해 DB에 저장돼 있으므로, 요청 시엔 *선택·순서·시간·이동수단*만 결정하면 된다. 이건 LLM 없이 결정적으로 푼다 — 다양성 우선 선택 → 최근접 이웃 정렬(지그재그 최소화) → 점심 슬롯 스왑 → haversine 거리로 교통·방위 계산. 고정 무드 10종의 임베딩도 precompute해 두어 **그 경우 OpenAI 호출도 0.** 결과: 빠르고, 싸고, 결정적이라 데모가 안 흔들린다.

- **품질이 필요할 땐 LLM로 승격.** `itinerary.planner: llm` 설정이면 Claude가 동선을 짠다. 이때 JSON 깨짐을 원천 차단하려고 **tool use(structured output)** 로 스키마를 강제하고, 실패 시 결정적 플래너로 폴백한다.

- **멱등 파이프라인.** 단계별 산출물을 `data/`에 저장하고 이미 적재된 `external_id`는 건너뛴다. 중간에 끊겨도 `--from <stage>`로 이어서 재개.

- **자동 품질 게이트.** 사람이 일일이 고르지 않는다. 프랜차이즈 블랙리스트 + "같은 이름이 N개 동네에 깔리면 체인"이라는 자동 탐지, 리뷰수 구간(너무 유명한 곳은 serendipity가 아니다), 동네×카테고리 균형 선별까지 규칙으로 거른다.

---

## 스택

FastAPI · Supabase(PostgreSQL + PostGIS + pgvector) · Claude API · OpenAI 임베딩 · React Native/Expo(앱) · Railway 배포

---

## 실행

```bash
pip install -r requirements.txt
cp .env.example .env        # 키 채우기 (Supabase / Anthropic / OpenAI / Kakao)
# Supabase SQL Editor에서 schema.sql 실행 (places 테이블 + match_places 함수)

# 데이터 파이프라인
python run_pipeline.py config/seoul.yaml --dry-run   # 적재 없이 점검
python run_pipeline.py config/seoul.yaml             # 실제 실행 (--from generate 등으로 재개)

# 일정 생성 API
uvicorn api.app:app --port 8000
curl -X POST localhost:8000/itinerary -H 'Content-Type: application/json' \
  -d '{"mood":"비 오는 날 혼자 사색하기 좋은","lat":37.5446,"lng":127.0559,"stops":4}'
```

### 검수 (아침 5분 루틴)

생성된 장소는 `pending`으로 들어오고, 사람이 훑어보고 승인한다:

```sql
select id, name, neighborhood, hint from places where status='pending' order by created_at;
update places set status='approved' where status='pending';   -- 일괄 승인
update places set status='rejected' where id in ('...');       -- 개별 반려
```

---

## 비용 감각 (서울 100곳 기준)

- 카카오 로컬 수집: 무료 (이 파이프라인은 ~1,350건, 일 한도 10만)
- Claude 생성: Sonnet 기준 수 달러 이내 (1회성)
- 임베딩: 1달러 미만
- **일정 생성 API: 요청당 사실상 0** (기본 결정적 플래너 + precompute된 무드 임베딩)

---

## 상태 / 다음

포트폴리오 데모 단계. 서울 100곳이 적재·검수되어 동선 생성까지 한 줄기로 동작한다. 다음: 새 도시 추가(`config/<city>.yaml`만 작성하면 동일 구조 재사용), 앱 reveal 인터랙션, 검수 어드민 엔드포인트.
