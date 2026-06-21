-- WanderWise places 스키마 (Supabase SQL Editor에서 실행)
-- 기존 places 테이블이 있으면 컬럼명만 맞춰 ALTER 하거나, 이 파일을 참고해 마이그레이션하세요.

create extension if not exists vector;
create extension if not exists postgis;

create table if not exists places (
  id            uuid primary key default gen_random_uuid(),
  external_id   text unique,
  source        text,
  name          text not null,
  display_name  text,
  city          text not null,
  neighborhood  text,
  category      text,
  address       text,
  lat           double precision,
  lng           double precision,
  location      geography(point, 4326),
  phone         text,
  url           text,
  rating        numeric,
  review_count  int,
  price_level   text,
  opening_hours jsonb,
  hint          text,
  description   text,
  reveal_text   text,
  moods         text[],
  embedding     vector(1536),          -- config의 embedding.dimensions와 일치해야 함
  status        text not null default 'pending',  -- pending | approved | rejected
  created_at    timestamptz default now()
);

-- lat/lng → PostGIS location 자동 동기화
create or replace function sync_location() returns trigger as $$
begin
  if new.lat is not null and new.lng is not null then
    new.location := st_setsrid(st_makepoint(new.lng, new.lat), 4326)::geography;
  end if;
  return new;
end; $$ language plpgsql;

drop trigger if exists places_sync_location on places;
create trigger places_sync_location
  before insert or update of lat, lng on places
  for each row execute function sync_location();

create index if not exists places_status_city_idx on places (city, status);
create index if not exists places_location_idx on places using gist (location);
create index if not exists places_embedding_idx on places
  using hnsw (embedding vector_cosine_ops);

-- 무드 임베딩 + 현재 위치로 후보 검색: 일정 생성 API의 핵심 쿼리
-- (분위기 유사도와 거리를 한 번에 — PostGIS × pgvector 조합)
create or replace function match_places(
  query_embedding vector(1536),
  user_lat double precision,
  user_lng double precision,
  max_distance_m double precision default 5000,
  match_count int default 20,
  target_city text default 'seoul'
) returns table (
  id uuid, name text, neighborhood text, category text,
  hint text, description text, reveal_text text, moods text[],
  lat double precision, lng double precision,
  distance_m double precision, similarity double precision
) language sql stable as $$
  select p.id, p.name, p.neighborhood, p.category,
         p.hint, p.description, p.reveal_text, p.moods,
         p.lat, p.lng,
         st_distance(p.location,
           st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography) as distance_m,
         1 - (p.embedding <=> query_embedding) as similarity
  from places p
  where p.status = 'approved'
    and p.city = target_city
    and st_dwithin(p.location,
          st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography, max_distance_m)
  order by p.embedding <=> query_embedding
  limit match_count;
$$;

-- 아침 5분 검수용: 대기 목록 보기 / 승인 / 반려
-- select id, name, neighborhood, category, hint from places where status = 'pending' order by created_at;
-- update places set status = 'approved' where status = 'pending';            -- 일괄 승인
-- update places set status = 'rejected' where id in ('...');                 -- 개별 반려
