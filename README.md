# WanderWise

> **Designed Serendipity** — 목적지의 *이름*은 도착하는 순간까지 숨깁니다. 무드를 고르면 방향·시적 힌트·하루 동선만 받고, 발걸음이 닿는 순간 그곳의 이름이 *공개(reveal)* 됩니다.

여행을 "검색해서 가는 것"에서 "이끌려 도착하는 것"으로 바꿔보고 싶어 만든 여행 앱입니다. 이 레포는 그 경험을 떠받치는 **데이터 파이프라인 + 일정 생성 API**입니다.

```
무드 선택  →  하루 동선 생성 (이름 숨김, 경로·시간은 투명)  →  도착 시 reveal
```

---

## 무엇을 풀려고 했는지

장소 추천 자체는 흔한 문제라고 생각합니다. 제가 어렵게 느꼈던 부분은 **"우연히 발견한 느낌"** 을 만드는 일이었습니다.

- 무드("비 오는 날 혼자 사색하기 좋은")를 **임베딩 유사도**로 장소와 매칭하고,
- 거리·시간·교통을 고려해 **지리적으로 말이 되는 하루 동선**으로 엮고,
- hint/방향엔 이름을 절대 흘리지 않으면서, **reveal 정보는 도착 시점에만** 꺼내 쓰도록 서버에서 구조적으로 분리했습니다.

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

응답 (발췌) — **도착 전 정보**와 **reveal**이 키로 분리되어 있습니다:

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

**엣지케이스**도 라우터에서 처리하고 있습니다: 빈 무드 `400`, 반경 내 후보 0개 `404`, 후보 < 요청 stops면 자동 축소 후 `"adjusted": true`.

---

## 어떻게 동작하는지

```
[ 서빙 ]  POST /itinerary
  무드 텍스트 ──임베딩──▶ match_places() ──후보 N──▶ 플래너 ──join──▶ 응답(reveal 분리)
                          │                          │
              PostGIS×pgvector 한 쿼리        기본: 결정적(LLM 0) / 옵션: Claude tool use

[ 파이프라인 ]  config/seoul.yaml 의 동네×카테고리 정의로 장소 DB를 채웁니다
  [1] collect   카카오 로컬 수집 (+선택: Google 평점·영업시간 보강)
  [2] filter    프랜차이즈·체인 자동 탐지, 평점 구간, 동네×카테고리 균형 선별
  [3] generate  Claude — 힌트·display_name·reveal 문장·무드 태그 (tool 검증 + 재시도)
  [4] embed     OpenAI/Voyage 임베딩 (pgvector)
  [5] load      Supabase upsert, status='pending' → 검수 후 approved
```

### 핵심 쿼리 — 분위기와 거리를 한 번에

`match_places()`(schema.sql)가 **pgvector 유사도 + PostGIS 거리 필터**를 단일 SQL로 처리합니다. 후처리 없이 "이 근처에서 이 무드에 맞는 곳"이 정렬되어 나옵니다.

```sql
order by p.embedding <=> query_embedding        -- 무드 유사도
where  st_dwithin(p.location, :user, :radius)    -- 거리
  and  p.status = 'approved' and p.city = :city  -- 검수된 것만
```

---

## 기술적 결정 (그리고 이유)

- **이름 숨김은 서버의 책임이라고 봤습니다.** hint·direction엔 `display_name`이 들어가지 못하게 하고, 실명은 `reveal` 블록에만 두었습니다. 프론트가 도착 판정 전까지 reveal을 렌더하지 않으면 컨셉이 깨지지 않습니다. LLM이 이름을 *다시 쓰는* 일을 막기 위해, 동선 플래너는 `place_id`만 다루고 이름·좌표·reveal은 DB 결과에서 join하도록 했습니다.

- **요청당 LLM 호출 0을 기본값으로 두었습니다.** 감성 텍스트(힌트·reveal)는 파이프라인에서 미리 생성해 DB에 저장해 두므로, 요청 시엔 *선택·순서·시간·이동수단*만 결정하면 됩니다. 이 부분은 LLM 없이 결정적으로 풀었습니다 — 다양성 우선 선택 → 최근접 이웃 정렬(지그재그 최소화) → 점심 슬롯 스왑 → haversine 거리로 교통·방위 계산. 고정 무드 10종의 임베딩도 precompute해 두어 **그 경우 OpenAI 호출도 0** 입니다. 덕분에 빠르고, 비용이 적고, 결정적이라 데모가 안정적으로 동작합니다.

- **품질이 필요할 땐 LLM으로 승격하도록 했습니다.** `itinerary.planner: llm` 설정이면 Claude가 동선을 짭니다. 이때 JSON 깨짐을 원천 차단하기 위해 **tool use(structured output)** 로 스키마를 강제하고, 실패 시 결정적 플래너로 폴백합니다.

- **멱등 파이프라인으로 만들었습니다.** 단계별 산출물을 `data/`에 저장하고 이미 적재된 `external_id`는 건너뜁니다. 중간에 끊겨도 `--from <stage>`로 이어서 재개할 수 있습니다.

- **품질 게이트를 자동화했습니다.** 사람이 일일이 고르지 않아도 되도록, 프랜차이즈 블랙리스트 + "같은 이름이 N개 동네에 깔리면 체인"이라는 자동 탐지, 리뷰수 구간(너무 유명한 곳은 serendipity가 아니라는 판단), 동네×카테고리 균형 선별을 규칙으로 처리합니다.

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

생성된 장소는 `pending`으로 들어오고, 사람이 훑어본 뒤 승인합니다.

```sql
select id, name, neighborhood, hint from places where status='pending' order by created_at;
update places set status='approved' where status='pending';   -- 일괄 승인
update places set status='rejected' where id in ('...');       -- 개별 반려
```

---

## 비용 감각

- 카카오 로컬 수집: 무료 (일 한도 10만 건)
- Claude 생성: 콘텐츠를 미리 생성하는 1회성 비용으로, 규모에 따라 수 달러 수준입니다
- 임베딩: 수백~수천 곳 기준 1달러 미만
- **일정 생성 API: 요청당 사실상 0** (기본 결정적 플래너 + precompute된 무드 임베딩)

---

## 데이터셋 확장 (scaling)

장소 밀도가 낮으면 한 동네 안에서 만들 수 있는 동선 조합이 금방 동납니다. 그래서 같은 파이프라인을 확장 가능한 형태로 다듬어, 서울 커버리지를 동네 16개에서 약 60개로 넓히고 후보를 대폭 늘렸습니다. 이 과정에서 비용을 관리하기 위해 아래를 적용했습니다.

- **동네 좌표 자동 지오코딩.** 상권 중심 좌표를 손으로 찍지 않고 카카오 검색 결과의 중앙값으로 잡아, 좌표 오류와 수작업을 줄였습니다. (`scripts/geocode_neighborhoods.py`)
- **무료 신호 우선, 유료 보강은 예산 캡.** 구조적 필터(프랜차이즈·체인·부속시설)와 LLM 품질 게이트로 후보를 먼저 거른 뒤, 평점·영업시간 같은 유료(Google) 보강은 **고정 호출 예산 안에서** 음식·술 카테고리를 우선해 채웠습니다. 소멸성 크레딧을 넘기지 않도록 하드 캡을 두었습니다. (`scripts/enrich_capped.py`)
- **검수 게이트 유지.** 새로 생성한 장소는 여전히 `pending`으로 적재되고, 사람이 검수해 `approved`로 올립니다. 규모가 커진 만큼 동네별로 훑어보고 일괄 승인하는 방식으로 운영합니다.

---

## 상태 / 다음

포트폴리오 데모 단계입니다. 현재 서울 700여 곳이 라이브로 동선 생성에 쓰이고 있고, 위 확장으로 약 5,900곳을 추가로 수집·생성·적재해 검수를 진행하고 있습니다. 검수가 끝나면 동네별 동선 다양성이 크게 늘어날 예정입니다. 이후로는 새 도시 추가(`config/<city>.yaml`만 작성하면 동일 구조 재사용), 앱 reveal 인터랙션, 검수 어드민 엔드포인트를 이어갈 계획입니다.
