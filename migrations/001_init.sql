-- Phase 0 migration: full schema per AGENT.md §8 (FR-2.1, FR-2.6, FR-3.9, FR-6.x, FR-8.x)
create extension if not exists "vector";
create extension if not exists "btree_gist";
create extension if not exists "pgcrypto";

create table if not exists visitors (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  last_seen_at timestamptz default now()
);

create table if not exists sessions (
  id uuid primary key default gen_random_uuid(),
  visitor_id uuid not null references visitors(id),
  status text not null default 'active' check (status in ('active','ended','abandoned','expired')),
  state jsonb not null default '{}',
  version int not null default 0,
  lease_owner text, lease_until timestamptz,
  timezone text,
  turn_count int not null default 0,
  last_activity_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '30 days')
);

create table if not exists messages (
  id bigserial primary key,
  session_id uuid not null references sessions(id),
  turn_id uuid,
  role text not null check (role in ('visitor','assistant','system')),
  content text not null,
  created_at timestamptz not null default now()
);

create table if not exists events (
  id bigserial primary key,
  session_id uuid not null, turn_id uuid, trace_id uuid not null,
  seq int not null, type text not null, agent text,
  payload jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists idx_events_session on events (session_id, id);

create table if not exists bookings (
  id uuid primary key default gen_random_uuid(),
  visitor_id uuid not null, session_id uuid not null,
  slot tstzrange not null,
  status text not null check (status in ('pending','confirmed','cancelled','failed')),
  calendar_event_id text unique, meet_url text, attendee_email text,
  created_at timestamptz default now(), updated_at timestamptz default now(),
  exclude using gist (slot with &&) where (status in ('pending','confirmed'))
);

create table if not exists lead_summaries (
  session_id uuid primary key,
  version int not null default 1,
  content jsonb not null, content_hash text not null,
  score int not null, tier text not null, incomplete boolean not null default false,
  updated_at timestamptz default now()
);

create table if not exists email_events (
  id bigserial primary key,
  session_id uuid not null, summary_version int not null,
  kind text not null check (kind in ('initial','update')),
  provider_message_id text,
  idempotency_key text not null unique,
  created_at timestamptz default now()
);

create table if not exists email_outbox (
  id bigserial primary key,
  session_id uuid not null, payload jsonb not null,
  status text not null default 'queued' check (status in ('queued','sending','sent','dead')),
  attempts int not null default 0, next_attempt_at timestamptz default now(),
  last_error text, idempotency_key text not null unique
);

create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  title text, source_page text, doc_type text, category text
);

-- EMBED_DIM=1536 must match config. Alter if model changes.
create table if not exists chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id),
  chunk_index int, content text not null,
  embedding vector(1536) not null,
  source_page text, section text, doc_type text,
  category text,
  token_count int
);
create index if not exists idx_chunks_embedding on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists idx_chunks_category on chunks (category);

create table if not exists llm_usage (
  id bigserial primary key, ts timestamptz default now(),
  session_id uuid, turn_id uuid, agent text, model text,
  input_tokens int, output_tokens int, cost_usd numeric(10,6)
);

-- Immutability: events and email_events reject UPDATE/DELETE (FR-6.7)
create or replace function reject_write() returns trigger as $$
begin raise exception 'append-only table'; end; $$ language plpgsql;
drop trigger if exists trg_events_immutable on events;
create trigger trg_events_immutable before update or delete on events
  for each row execute function reject_write();
drop trigger if exists trg_email_events_immutable on email_events;
create trigger trg_email_events_immutable before update or delete on email_events
  for each row execute function reject_write();

-- RLS on, no anon policies (server uses DATABASE_URL role only)
alter table visitors enable row level security;
alter table sessions enable row level security;
alter table messages enable row level security;
alter table events enable row level security;
alter table bookings enable row level security;
alter table lead_summaries enable row level security;
alter table email_events enable row level security;
alter table email_outbox enable row level security;
alter table documents enable row level security;
alter table chunks enable row level security;
alter table llm_usage enable row level security;
