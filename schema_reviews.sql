-- WanderWise Phase 2: 리뷰 + 프로필 + 사진 스토리지 (Supabase SQL Editor에서 실행)
-- 프론트(supabase-js, anon 공개 키)에서 직접 접근하므로 보안은 RLS로 강제한다.

-- ── 프로필 (auth.users 1:1) ──────────────────────────────────
create table if not exists profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  created_at   timestamptz default now()
);

alter table profiles enable row level security;

drop policy if exists "프로필 공개 읽기" on profiles;
create policy "프로필 공개 읽기" on profiles for select using (true);

drop policy if exists "본인 프로필 생성" on profiles;
create policy "본인 프로필 생성" on profiles for insert with check (auth.uid() = id);

drop policy if exists "본인 프로필 수정" on profiles;
create policy "본인 프로필 수정" on profiles for update using (auth.uid() = id);

-- 가입 시 프로필 자동 생성 (닉네임은 메타데이터, 없으면 이메일 앞부분)
-- security definer 실행 컨텍스트에선 search_path에 public이 없을 수 있어 명시 + 스키마 한정 필수.
-- (안 하면 가입이 "Database error saving new user"로 실패)
create or replace function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'display_name', split_part(new.email, '@', 1)));
  return new;
end; $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- ── 리뷰 ─────────────────────────────────────────────────────
create table if not exists reviews (
  id         uuid primary key default gen_random_uuid(),
  place_id   uuid not null references places(id) on delete cascade,
  user_id    uuid not null references profiles(id) on delete cascade,  -- 작성자(=profiles.id=auth.uid)
  body       text not null,
  photos     text[] default '{}',   -- Storage 객체 경로 (review-photos 버킷)
  created_at timestamptz default now()
);

create index if not exists reviews_place_idx on reviews (place_id, created_at desc);
create index if not exists reviews_user_idx  on reviews (user_id);

alter table reviews enable row level security;

-- 읽기: 공개 (앱에서는 '도착한 장소'에서만 조회하도록 UX로 제한)
drop policy if exists "리뷰 공개 읽기" on reviews;
create policy "리뷰 공개 읽기" on reviews for select using (true);

drop policy if exists "본인 리뷰 작성" on reviews;
create policy "본인 리뷰 작성" on reviews for insert with check (auth.uid() = user_id);

drop policy if exists "본인 리뷰 수정" on reviews;
create policy "본인 리뷰 수정" on reviews for update using (auth.uid() = user_id);

drop policy if exists "본인 리뷰 삭제" on reviews;
create policy "본인 리뷰 삭제" on reviews for delete using (auth.uid() = user_id);

-- ── 사진 스토리지 버킷 ───────────────────────────────────────
insert into storage.buckets (id, name, public)
values ('review-photos', 'review-photos', true)
on conflict (id) do nothing;

drop policy if exists "리뷰사진 공개 읽기" on storage.objects;
create policy "리뷰사진 공개 읽기" on storage.objects
  for select using (bucket_id = 'review-photos');

-- 업로드/삭제: 로그인 사용자, 본인 uid 폴더({uid}/파일)에만
drop policy if exists "리뷰사진 본인 업로드" on storage.objects;
create policy "리뷰사진 본인 업로드" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'review-photos' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "리뷰사진 본인 삭제" on storage.objects;
create policy "리뷰사진 본인 삭제" on storage.objects
  for delete to authenticated
  using (bucket_id = 'review-photos' and (storage.foldername(name))[1] = auth.uid()::text);
