-- 002: human-stable chunk key for ingest upserts (uuid PK stays default)
alter table chunks add column if not exists slug text unique;
