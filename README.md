# WanderWise Data Pipeline

동네 × 카테고리 조합으로 장소를 자동 수집하고, 품질 필터 → Claude 시적 묘사 생성 → 임베딩 → Supabase 적재까지 한 번에 처리하는 파이프라인.

```
config/seoul.yaml (동네·무드·필터 정의)
        │
  [1] collect   카카오 로컬 API 수집 (+선택: Google 평점/영업시간 보강)
  [2] filter    프랜차이즈·카테고리 블랙리스트, 평점 구간, 중복/기존 데이터 제외
  [3] generate  Claude API — 힌트·묘사·reveal 문장·무드 태그 (JSON 검증 + 재시도)
  [4] embed     OpenAI/Voyage 임베딩 (pgvector용)
  [5] load      Supabase upsert, status='pending' → 검수 후 approved
        │
GitHub Actions cron (주 1회 자동 실행)
```

## 시작하기

```bash
pip install -r requirements.txt
cp .env.example .env        # 키 채우기
# Supabase SQL Editor에서 schema.sql 실행 (places 테이블 + match_places 함수)
python run_pipeline.py config/seoul.yaml --dry-run   # 적재 없이 점검
python run_pipeline.py config/seoul.yaml             # 실제 실행
```

중간부터 재개: `--from generate` / `--from embed` 등. 단계별 결과는 `data/`에 저장되고, 이미 Supabase에 있는 장소는 자동으로 건너뜁니다(멱등).

## 검수 (아침 5분 루틴)

```sql
select id, name, neighborhood, hint from places where status='pending' order by created_at;
update places set status='approved' where status='pending';        -- 전체 승인
update places set status='rejected' where id in ('...');            -- 개별 반려
```

FastAPI에 검수 엔드포인트를 붙이려면:

```python
@router.get("/admin/pending")
def pending():
    return sb.table("places").select("id,name,neighborhood,category,hint,reveal_text") \
             .eq("status", "pending").execute().data

@router.post("/admin/review")
def review(place_id: str, approve: bool):
    sb.table("places").update({"status": "approved" if approve else "rejected"}) \
      .eq("id", place_id).execute()
```

## 일정 생성 API에서 쓰는 핵심 쿼리

`schema.sql`의 `match_places()` 함수가 무드 임베딩 유사도 + 거리 필터를 한 번에 처리합니다:

```python
mood_embedding = embed("비 오는 날의 쓸쓸하고 사색적인 분위기")
candidates = sb.rpc("match_places", {
    "query_embedding": mood_embedding,
    "user_lat": lat, "user_lng": lng,
    "max_distance_m": 4000, "match_count": 20,
}).execute().data
```

## 새 도시 추가

`config/lisbon.yaml`을 만들고 `collector: google`로 두면 같은 구조 재사용 가능 (Google 수집기는 collect.py의 `google_enrich`를 text search로 확장하면 됨 — 현재는 카카오 수집 + Google 보강 구조).

## 비용 감각 (서울 100곳 기준)

- 카카오 로컬: 무료 (일 10만 건 한도, 이 파이프라인은 ~1,350건)
- Claude 생성: Sonnet 기준 수 달러 이내, Haiku면 그 이하
- 임베딩: 1달러 미만
