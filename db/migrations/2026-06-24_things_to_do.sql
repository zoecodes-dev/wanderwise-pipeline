-- things_to_do 컬럼 추가 + match_places 반환에 포함
-- Supabase SQL Editor에서 실행하세요. (그 다음 파이프라인이 값을 채웁니다)

alter table places add column if not exists things_to_do text;

-- 반환 타입(컬럼 추가)을 바꾸므로 create or replace 불가 → 먼저 drop
drop function if exists match_places(vector, double precision, double precision, double precision, integer, text);

create or replace function match_places(
  query_embedding vector(1536),
  user_lat double precision,
  user_lng double precision,
  max_distance_m double precision default 5000,
  match_count int default 20,
  target_city text default 'seoul'
) returns table (
  id uuid, name text, neighborhood text, category text,
  hint text, description text, reveal_text text, things_to_do text, moods text[],
  lat double precision, lng double precision,
  distance_m double precision, similarity double precision
) language sql stable as $$
  select p.id, p.name, p.neighborhood, p.category,
         p.hint, p.description, p.reveal_text, p.things_to_do, p.moods,
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
