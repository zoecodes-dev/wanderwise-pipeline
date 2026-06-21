# WanderWise — 프로젝트 컨텍스트

## 컨셉
여행 앱. "Designed Serendipity" — 목적지 이름을 도착 전까지 숨기고, 방향·시적 힌트·하루 동선으로 여행자를 안내한다. 도착 순간 이름이 공개(reveal)되는 경험이 핵심.

## 현재 목표 (포트폴리오 데모)
무드 선택 → 하루 동선 생성(이름 숨김, 경로·교통·시간은 투명) → 도착 시 reveal. 이 한 줄기가 영상으로 찍힐 만큼 매끄럽게 작동하는 것이 목표. 베타·고도화는 그 다음 단계.

## 스택
- 백엔드: FastAPI on Railway, Supabase(PostgreSQL + PostGIS + pgvector)
- 프론트: React Native / Expo
- AI: 임베딩(무드↔장소 매칭), Claude API(묘사 생성·동선 구성)

## 데이터 파이프라인 (이 레포)
config의 동네×카테고리 조합 → 카카오 로컬 수집 → 품질 필터(target_count로 동네별 균등 선별) → Claude 생성(hint/display_name/reveal_text/moods) → 임베딩 → Supabase 적재(status=pending → 검수 후 approved).
- 실행: `python run_pipeline.py config/seoul.yaml [--from STAGE] [--dry-run]`
- 단계별 중간 산출물은 data/ 에 저장, 재실행 시 이미 만든 건 건너뜀(멱등)
- 핵심 DB 함수: `match_places()` — 무드 임베딩 유사도 + 거리 필터를 한 쿼리로

## 작업 규칙 (중요)
- `.env`는 읽되 절대 출력·커밋하지 말 것. SUPABASE_SERVICE_KEY는 특히 민감.
- 변경은 작은 단위로. 큰 변경 전엔 무엇을 왜 바꾸는지 먼저 설명할 것.
- 데이터·DB를 건드리는 작업(적재, 삭제, 스키마 변경)은 실행 전 확인받을 것.
- generate는 Claude API 크레딧을 쓴다. 대량 실행 전 작은 범위(test config)로 검증.
- 결과를 "완료"로 보고하기 전에 실제 출력을 확인할 것. (과거에 옛 캐시를 새 결과로 착각한 적 있음)

## 다음 작업 (2일차)
일정 생성 API: 무드 입력 → match_places로 후보 추출 → Claude가 하루 동선 구성(시간·거리·교통 고려) → reveal 정보 포함해 반환.
