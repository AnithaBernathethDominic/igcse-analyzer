alter table questions
add column if not exists option_a text,
add column if not exists option_b text,
add column if not exists option_c text,
add column if not exists option_d text,
add column if not exists marks int,
add column if not exists image_url text,
add column if not exists raw_text text,
add column if not exists needs_review boolean default false,
add column if not exists extraction_confidence text default 'good';

insert into storage.buckets (id, name, public)
values ('question-images', 'question-images', true)
on conflict (id) do update set public = excluded.public;
