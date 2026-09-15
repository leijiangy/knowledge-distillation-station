-- 知识蒸馏站：用户私有解释、周期 Token 额度与积分结算。
-- 唯一实际扣量来源：供应商响应 usage.total_tokens；1 Token = 1 积分。
--
-- 本迁移只定义目标结构和 PostgREST RPC，不会由应用自动执行。先在备份副本
-- 或隔离验收库演练，再由运维人员显式执行。所有写 RPC 只接受 service_role。

begin;

create extension if not exists pgcrypto;

create table if not exists billing_settings (
  id smallint primary key check (id = 1),
  billing_enabled boolean not null default false,
  billing_rule text not null check (billing_rule = 'total_tokens_1_to_1'),
  text_model text not null check (text_model = 'deepseek-flash'),
  quota_timezone text not null check (quota_timezone = 'Asia/Shanghai'),
  quota_reset_hour smallint not null check (quota_reset_hour = 4),
  standard_period_tokens bigint not null check (standard_period_tokens = 50000),
  premium_period_tokens bigint not null check (premium_period_tokens = 500000),
  premium_monthly_price_fen bigint not null check (premium_monthly_price_fen = 1990),
  updated_at timestamptz not null default now()
);

insert into billing_settings (
  id, billing_enabled, billing_rule, text_model, quota_timezone,
  quota_reset_hour, standard_period_tokens, premium_period_tokens,
  premium_monthly_price_fen
) values (
  1, false, 'total_tokens_1_to_1', 'deepseek-flash', 'Asia/Shanghai',
  4, 50000, 500000, 1990
) on conflict (id) do nothing;

create table if not exists membership_entitlements (
  id uuid primary key,
  uid text not null,
  tier text not null check (tier = 'premium'),
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  status text not null check (status in ('active', 'expired', 'revoked')),
  source text not null,
  provider_ref text null,
  idempotency_key uuid null,
  request_hash text null,
  amount_fen bigint null check (amount_fen is null or amount_fen = 1990),
  created_at timestamptz not null default now(),
  check (ends_at > starts_at)
);

create index if not exists membership_entitlements_uid_window_idx
  on membership_entitlements (uid, starts_at, ends_at);

alter table membership_entitlements
  add column if not exists idempotency_key uuid null;
alter table membership_entitlements
  add column if not exists request_hash text null;
alter table membership_entitlements
  add column if not exists amount_fen bigint null;
create unique index if not exists membership_entitlements_uid_idempotency_uidx
  on membership_entitlements (uid, idempotency_key)
  where idempotency_key is not null;

create table if not exists daily_token_quotas (
  uid text not null,
  quota_period date not null,
  membership_tier text not null check (membership_tier in ('standard', 'premium')),
  limit_tokens bigint not null check (limit_tokens >= 0),
  used_tokens bigint not null default 0 check (used_tokens >= 0),
  reserved_tokens bigint not null default 0 check (reserved_tokens >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (uid, quota_period),
  check (used_tokens + reserved_tokens <= limit_tokens)
);

create table if not exists credit_wallets (
  uid text primary key,
  balance_microcredits bigint not null default 0 check (balance_microcredits >= 0),
  ai_reserved_microcredits bigint not null default 0 check (ai_reserved_microcredits >= 0),
  refund_reserved_microcredits bigint not null default 0 check (refund_reserved_microcredits >= 0),
  updated_at timestamptz not null default now(),
  check (balance_microcredits >= ai_reserved_microcredits + refund_reserved_microcredits)
);

create table if not exists ai_operations (
  id uuid primary key,
  uid text not null,
  payer_scope text not null default 'user' check (payer_scope = 'user'),
  action text not null check (action in ('init', 'explain', 'ask', 'advanced')),
  scope_key text not null,
  idempotency_key uuid not null,
  request_hash text not null,
  effective_input_hash text not null,
  active_key text null,
  article_key text not null,
  git_commit text not null,
  segment_id uuid not null,
  parent_id bigint null,
  replace_node_id bigint null,
  intent_json jsonb not null,
  messages_json jsonb null,
  status text not null check (
    status in (
      'quoted', 'reserved', 'dispatched', 'result_recorded', 'settled',
      'cache_hit', 'quote_expired', 'waived_not_dispatched',
      'waived_failure', 'waived_unknown'
    )
  ),
  created_at timestamptz not null default now(),
  quote_expires_at timestamptz not null,
  budget_valid_until timestamptz not null,
  quoted_input_token_upper_bound bigint not null check (quoted_input_token_upper_bound >= 0),
  output_limit_tokens bigint not null check (output_limit_tokens > 0),
  quoted_total_tokens bigint not null check (quoted_total_tokens >= 0),
  quoted_microcredits bigint not null check (quoted_microcredits >= 0),
  billing_policy jsonb not null,
  quota_period date null,
  membership_tier text null check (membership_tier is null or membership_tier in ('standard', 'premium')),
  quota_limit_tokens bigint not null default 0 check (quota_limit_tokens >= 0),
  reserved_daily_tokens bigint not null default 0 check (reserved_daily_tokens >= 0),
  reserved_wallet_microcredits bigint not null default 0 check (reserved_wallet_microcredits >= 0),
  reserved_total_tokens bigint not null default 0 check (reserved_total_tokens >= 0),
  charged_daily_tokens bigint not null default 0 check (charged_daily_tokens >= 0),
  charged_wallet_microcredits bigint not null default 0 check (charged_wallet_microcredits >= 0),
  usage_tokens bigint null check (usage_tokens is null or usage_tokens >= 0),
  requested_model text not null check (requested_model = 'deepseek-flash'),
  returned_model text null,
  provider_response_id text null,
  usage_json jsonb null,
  result_json jsonb null,
  result_hash text null,
  result_node_id bigint null,
  executor_token uuid null,
  dispatch_started_at timestamptz null,
  deadline_at timestamptz null,
  result_recorded_at timestamptz null,
  settled_at timestamptz null,
  error_code text null,
  unique (scope_key, action, idempotency_key),
  check (quoted_microcredits = quoted_total_tokens * 1000000),
  check (reserved_wallet_microcredits % 1000000 = 0),
  check (charged_wallet_microcredits % 1000000 = 0)
);

create unique index if not exists ai_operations_active_intent_uidx
  on ai_operations (scope_key, active_key)
  where status in ('reserved', 'dispatched', 'result_recorded');

create index if not exists ai_operations_recovery_idx
  on ai_operations (status, deadline_at, created_at);

create table if not exists credit_ledger (
  id bigint generated by default as identity primary key,
  uid text not null,
  event_key text not null unique,
  kind text not null check (
    kind in ('ai_reserve', 'ai_settle', 'ai_release')
  ),
  ai_operation_id uuid null references ai_operations(id),
  payment_order_id uuid null,
  refund_id uuid null,
  quota_period date null,
  daily_used_delta bigint not null default 0,
  daily_reserved_delta bigint not null default 0,
  daily_used_after bigint null,
  daily_reserved_after bigint null,
  balance_delta bigint not null default 0,
  ai_reserved_delta bigint not null default 0,
  refund_reserved_delta bigint not null default 0,
  balance_after bigint not null,
  ai_reserved_after bigint not null,
  refund_reserved_after bigint not null,
  created_at timestamptz not null default now()
);

create index if not exists credit_ledger_uid_id_idx on credit_ledger (uid, id desc);

create table if not exists content_updates (
  id uuid primary key,
  uid text not null,
  article_key text not null,
  idempotency_key uuid not null,
  request_hash text not null,
  expected_commit text null,
  candidate_json jsonb not null,
  candidate_commit text null,
  state text not null check (state in ('queued','building','git_saved','published','conflict','failed')),
  executor_token uuid null,
  lease_until timestamptz null,
  deadline_at timestamptz null,
  ai_operation_id uuid null references ai_operations(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  published_at timestamptz null,
  error_code text null,
  unique(uid,idempotency_key)
);

create index if not exists content_updates_state_lease_idx
  on content_updates(state,lease_until,created_at);

-- 兼容早期七表：只追加结算和版本所需列，不猜测或删除线上约束。
do $$
begin
  if to_regclass('public.distilled') is null
     or to_regclass('public.reading_articles') is null
     or to_regclass('public.reading_nodes') is null then
    raise exception using errcode='55000',
      message='BASE_SCHEMA_REQUIRED: distilled/reading_articles/reading_nodes';
  end if;
end;
$$;

alter table reading_articles
  add column if not exists segments jsonb,
  add column if not exists git_commit text,
  add column if not exists parent_commit text,
  add column if not exists update_id uuid,
  add column if not exists manifest_sha256 text,
  add column if not exists published_at timestamptz;

alter table distilled
  add column if not exists current_commit text,
  add column if not exists updated_by_uid text;

alter table reading_nodes
  add column if not exists segment_id uuid,
  add column if not exists generated_commit text,
  add column if not exists operation_id uuid,
  add column if not exists supersedes_id bigint,
  add column if not exists is_current boolean not null default true,
  add column if not exists deleted_at timestamptz,
  add column if not exists migrated_from_id bigint;

create unique index if not exists reading_nodes_operation_uidx
  on reading_nodes (operation_id) where operation_id is not null;
create unique index if not exists reading_nodes_identity_uidx
  on reading_nodes (id, uid, article_key, segment_id);
create unique index if not exists reading_nodes_active_init_uidx
  on reading_nodes (uid, article_key, segment_id)
  where kind = 'init' and is_current and deleted_at is null;
create index if not exists reading_nodes_scope_parent_idx
  on reading_nodes (uid, article_key, segment_id, parent_id);

create unique index if not exists reading_articles_article_commit_uidx
  on reading_articles(article_key,git_commit);
create unique index if not exists reading_articles_update_uidx
  on reading_articles(update_id) where update_id is not null;

-- 旧表是部署前置。只接受应用当前确实使用的列；不自动猜列名或改列型。
do $$
declare
  v_pk_name text;
  v_pk_columns text[];
begin
  if to_regclass('public.distilled') is null
     or to_regclass('public.reading_articles') is null
     or to_regclass('public.reading_nodes') is null then
    raise exception using errcode='55000',
      message='BASE_SCHEMA_REQUIRED: distilled/reading_articles/reading_nodes';
  end if;
  if exists(select 1 from reading_articles where git_commit is null)
     or exists(select 1 from reading_articles where segments is null) then
    raise exception using errcode='55000',
      message='VERSION_BACKFILL_REQUIRED: reading_articles.git_commit/segments';
  end if;
  if exists(select 1 from reading_nodes where segment_id is null or generated_commit is null) then
    raise exception using errcode='55000',
      message='VERSION_BACKFILL_REQUIRED: reading_nodes.segment_id/generated_commit';
  end if;
  alter table public.reading_articles
    alter column article_key set not null,
    alter column git_commit set not null,
    alter column segments set not null;
  alter table public.reading_nodes
    alter column segment_id set not null,
    alter column generated_commit set not null;
  select c.conname,array_agg(a.attname order by u.ordinality)
    into v_pk_name,v_pk_columns
  from pg_constraint c
  join unnest(c.conkey) with ordinality u(attnum,ordinality) on true
  join pg_attribute a on a.attrelid=c.conrelid and a.attnum=u.attnum
  where c.conrelid='public.reading_articles'::regclass and c.contype='p'
  group by c.conname;
  if v_pk_name is not null and v_pk_columns=array['article_key']::text[] then
    execute format('alter table public.reading_articles drop constraint %I',v_pk_name);
    alter table public.reading_articles
      add constraint reading_articles_article_commit_pk primary key
      using index reading_articles_article_commit_uidx;
  elsif v_pk_columns is distinct from array['article_key','git_commit']::text[] then
    raise exception using errcode='55000',
      message='UNSUPPORTED_READING_ARTICLES_PRIMARY_KEY';
  end if;
end;
$$;

do $$
begin
  if not exists(select 1 from pg_constraint where conname='reading_nodes_parent_scope_fk'
                and conrelid='public.reading_nodes'::regclass) then
    alter table public.reading_nodes add constraint reading_nodes_parent_scope_fk
      foreign key(parent_id,uid,article_key,segment_id)
      references public.reading_nodes(id,uid,article_key,segment_id);
  end if;
  if not exists(select 1 from pg_constraint where conname='reading_nodes_supersedes_scope_fk'
                and conrelid='public.reading_nodes'::regclass) then
    alter table public.reading_nodes add constraint reading_nodes_supersedes_scope_fk
      foreign key(supersedes_id,uid,article_key,segment_id)
      references public.reading_nodes(id,uid,article_key,segment_id);
  end if;
  if not exists(select 1 from pg_constraint where conname='reading_nodes_operation_fk'
                and conrelid='public.reading_nodes'::regclass) then
    alter table public.reading_nodes add constraint reading_nodes_operation_fk
      foreign key(operation_id) references public.ai_operations(id);
  end if;
  if not exists(select 1 from pg_constraint where conname='ai_operations_parent_scope_fk'
                and conrelid='public.ai_operations'::regclass) then
    alter table public.ai_operations add constraint ai_operations_parent_scope_fk
      foreign key(parent_id,uid,article_key,segment_id)
      references public.reading_nodes(id,uid,article_key,segment_id);
  end if;
  if not exists(select 1 from pg_constraint where conname='ai_operations_replace_scope_fk'
                and conrelid='public.ai_operations'::regclass) then
    alter table public.ai_operations add constraint ai_operations_replace_scope_fk
      foreign key(replace_node_id,uid,article_key,segment_id)
      references public.reading_nodes(id,uid,article_key,segment_id);
  end if;
  if not exists(select 1 from pg_constraint where conname='ai_operations_result_node_fk'
                and conrelid='public.ai_operations'::regclass) then
    alter table public.ai_operations add constraint ai_operations_result_node_fk
      foreign key(result_node_id) references public.reading_nodes(id);
  end if;
end;
$$;

create unique index if not exists reading_nodes_supersedes_uidx
  on reading_nodes(supersedes_id) where supersedes_id is not null;

create or replace function quota_period_at(p_at timestamptz)
returns date language sql stable strict as $$
  select ((p_at at time zone 'Asia/Shanghai') - interval '4 hours')::date
$$;

create or replace function _billing_assert_service_role()
returns void language plpgsql stable as $$
declare
  v_role text;
  v_claims text;
begin
  v_role := nullif(current_setting('request.jwt.claim.role', true), '');
  v_claims := nullif(current_setting('request.jwt.claims', true), '');
  if v_role is null and v_claims is not null then
    v_role := (v_claims::jsonb ->> 'role');
  end if;
  if v_role is distinct from 'service_role' then
    raise exception using errcode = '42501', message = 'SERVICE_ROLE_REQUIRED';
  end if;
end;
$$;

create or replace function _billing_sha256(p_value jsonb)
returns text language sql immutable strict as $$
  select encode(digest(convert_to(p_value::text, 'UTF8'), 'sha256'), 'hex')
$$;

create or replace function _billing_active_key(p_action text, p_intent jsonb, p_request_hash text)
returns text language sql immutable strict as $$
  select case
    when p_action = 'advanced' then p_request_hash
    else _billing_sha256(jsonb_build_object(
      'action', p_action,
      'article_key', p_intent ->> 'article_key',
      'segment_id', p_intent ->> 'segment_id',
      'parent_id', p_intent -> 'parent_id',
      'replace_node_id', p_intent -> 'replace_node_id',
      'pos_start', p_intent -> 'pos_start',
      'pos_end', p_intent -> 'pos_end',
      'question', p_intent -> 'question'
    ))
  end
$$;

create or replace function _reading_segment_index(
  p_article_key text, p_git_commit text, p_segment_id uuid
) returns integer language sql stable as $$
  with source as (
    select case
      when jsonb_typeof(segments) = 'array' then segments
      when jsonb_typeof(segments -> 'segments') = 'array' then segments -> 'segments'
      else '[]'::jsonb
    end as rows
    from reading_articles
    where article_key = p_article_key and git_commit = p_git_commit
  )
  select (item.ordinality - 1)::integer
  from source, jsonb_array_elements(source.rows) with ordinality as item(value, ordinality)
  where item.value ->> 'segment_id' = p_segment_id::text
  limit 1
$$;

create or replace function _reading_branch_visible(
  p_node_id bigint, p_uid text, p_article_key text, p_segment_id uuid,
  p_include_history boolean default false
) returns boolean language sql stable as $$
  with recursive chain as (
    select n.id, n.parent_id, n.uid, n.article_key, n.segment_id,
           n.is_current, n.deleted_at, false as bad
    from reading_nodes n where n.id = p_node_id
    union all
    select p.id, p.parent_id, p.uid, p.article_key, p.segment_id,
           p.is_current, p.deleted_at,
           c.bad or p.uid is distinct from p_uid
             or p.article_key is distinct from p_article_key
             or p.segment_id is distinct from p_segment_id
    from chain c join reading_nodes p on p.id = c.parent_id
    where not c.bad
  )
  select exists(select 1 from chain)
    and not exists (
      select 1 from chain
      where bad or uid is distinct from p_uid
        or article_key is distinct from p_article_key
        or segment_id is distinct from p_segment_id
        or deleted_at is not null
        or (not p_include_history and not is_current)
    )
    and exists(select 1 from chain where parent_id is null)
$$;

create or replace function _billing_result_is_valid(p_op ai_operations)
returns boolean language plpgsql stable as $$
declare
  v_usage jsonb;
  v_total numeric;
  v_prompt numeric;
  v_completion numeric;
  v_reasoning numeric;
begin
  if p_op.result_json is null
     or jsonb_typeof(p_op.result_json) <> 'object'
     or nullif(btrim(p_op.result_json ->> 'content'), '') is null
     or p_op.result_json ->> 'finish_reason' <> 'stop'
     or p_op.result_json ->> 'returned_model' <> 'deepseek-flash'
     or not (p_op.billing_policy -> 'allowed_returned_models' @> '["deepseek-flash"]'::jsonb)
  then return false; end if;
  v_usage := p_op.result_json -> 'usage';
  if jsonb_typeof(v_usage) <> 'object'
     or jsonb_typeof(v_usage -> 'total_tokens') <> 'number'
  then return false; end if;
  begin v_total := (v_usage ->> 'total_tokens')::numeric;
  exception when others then return false; end;
  if v_total <> trunc(v_total) or v_total < 0 or v_total > p_op.quoted_total_tokens
  then return false; end if;

  if v_usage ? 'prompt_tokens' then
    if jsonb_typeof(v_usage -> 'prompt_tokens') <> 'number' then return false; end if;
    begin v_prompt := (v_usage ->> 'prompt_tokens')::numeric;
    exception when others then return false; end;
    if v_prompt <> trunc(v_prompt) or v_prompt < 0
       or v_prompt > p_op.quoted_input_token_upper_bound then return false; end if;
  end if;
  if v_usage ? 'completion_tokens' then
    if jsonb_typeof(v_usage -> 'completion_tokens') <> 'number' then return false; end if;
    begin v_completion := (v_usage ->> 'completion_tokens')::numeric;
    exception when others then return false; end;
    if v_completion <> trunc(v_completion) or v_completion < 0
       or v_completion > p_op.output_limit_tokens then return false; end if;
  end if;
  if v_prompt is not null and v_completion is not null
     and v_total <> v_prompt + v_completion then return false; end if;

  if v_usage ? 'reasoning_tokens' then
    if jsonb_typeof(v_usage -> 'reasoning_tokens') <> 'number' then return false; end if;
    begin v_reasoning := (v_usage ->> 'reasoning_tokens')::numeric;
    exception when others then return false; end;
    if v_reasoning <> trunc(v_reasoning) or v_reasoning < 0
       or (v_completion is not null and v_reasoning > v_completion) then return false; end if;
  end if;
  if v_usage ? 'cache_hit_tokens' and (
       jsonb_typeof(v_usage -> 'cache_hit_tokens') <> 'number'
       or (v_usage ->> 'cache_hit_tokens')::numeric < 0
       or (v_usage ->> 'cache_hit_tokens')::numeric <> trunc((v_usage ->> 'cache_hit_tokens')::numeric)
     ) then return false; end if;
  if v_usage ? 'cache_miss_tokens' and (
       jsonb_typeof(v_usage -> 'cache_miss_tokens') <> 'number'
       or (v_usage ->> 'cache_miss_tokens')::numeric < 0
       or (v_usage ->> 'cache_miss_tokens')::numeric <> trunc((v_usage ->> 'cache_miss_tokens')::numeric)
     ) then return false; end if;
  return v_total <= 9223372036854775807;
exception when others then
  return false;
end;
$$;

create or replace function get_billing_account(actor_uid text)
returns jsonb language plpgsql security invoker as $$
declare
  v_now timestamptz := clock_timestamp();
  v_period date;
  v_tier text := 'standard';
  v_expires timestamptz;
  v_limit bigint := 50000;
  v_daily daily_token_quotas%rowtype;
  v_wallet credit_wallets%rowtype;
begin
  perform _billing_assert_service_role();
  if nullif(btrim(actor_uid), '') is null then
    raise exception using errcode = '22023', message = 'INVALID_UID';
  end if;
  select e.ends_at into v_expires
  from membership_entitlements e
  where e.uid = actor_uid and e.tier = 'premium' and e.status = 'active'
    and e.starts_at <= v_now and e.ends_at > v_now
  order by e.ends_at desc, e.id limit 1 for update;
  if found then v_tier := 'premium'; v_limit := 500000; end if;
  v_period := quota_period_at(v_now);
  insert into daily_token_quotas(uid, quota_period, membership_tier, limit_tokens)
  values (actor_uid, v_period, v_tier, v_limit)
  on conflict (uid, quota_period) do update set
    membership_tier = case when excluded.limit_tokens > daily_token_quotas.limit_tokens
                           then excluded.membership_tier else daily_token_quotas.membership_tier end,
    limit_tokens = greatest(daily_token_quotas.limit_tokens, excluded.limit_tokens),
    updated_at = now();
  select * into v_daily from daily_token_quotas
  where uid = actor_uid and quota_period = v_period for update;
  insert into credit_wallets(uid) values (actor_uid) on conflict (uid) do nothing;
  select * into v_wallet from credit_wallets where uid = actor_uid for update;
  return jsonb_build_object(
    'membership_tier', v_tier, 'membership_expires_at', v_expires,
    'quota_period', v_period, 'daily_limit_tokens', v_daily.limit_tokens,
    'daily_used_tokens', v_daily.used_tokens,
    'daily_reserved_tokens', v_daily.reserved_tokens,
    'daily_available_tokens', v_daily.limit_tokens-v_daily.used_tokens-v_daily.reserved_tokens,
    'balance_microcredits', v_wallet.balance_microcredits,
    'ai_reserved_microcredits', v_wallet.ai_reserved_microcredits,
    'refund_reserved_microcredits', v_wallet.refund_reserved_microcredits,
    'available_microcredits', v_wallet.balance_microcredits-v_wallet.ai_reserved_microcredits-v_wallet.refund_reserved_microcredits
  );
end;
$$;

create or replace function apply_demo_membership(
  actor_uid text, p_entitlement_id uuid, p_idempotency_key uuid,
  p_request_hash text, p_amount_fen bigint
) returns jsonb language plpgsql security invoker as $$
declare
  v_existing membership_entitlements%rowtype;
  v_now timestamptz := clock_timestamp();
  v_current_expires timestamptz;
  v_starts timestamptz;
  v_ends timestamptz;
begin
  perform _billing_assert_service_role();
  if nullif(btrim(actor_uid),'') is null
     or nullif(btrim(p_request_hash),'') is null
     or p_amount_fen <> 1990
  then
    raise exception using errcode='22023',message='INVALID_INPUT';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('demo-membership|' || actor_uid, 0)
  );

  select * into v_existing
  from membership_entitlements
  where uid=actor_uid and idempotency_key=p_idempotency_key;

  if found then
    if v_existing.request_hash is distinct from p_request_hash
       or v_existing.amount_fen is distinct from p_amount_fen
       or v_existing.source is distinct from 'demo_purchase'
    then
      return jsonb_build_object('error_code','IDEMPOTENCY_CONFLICT');
    end if;
    select max(ends_at) into v_current_expires
    from membership_entitlements
    where uid=actor_uid and tier='premium' and status='active'
      and ends_at>v_now;
    return jsonb_build_object(
      'id',v_existing.id,'activated',false,'tier','premium',
      'membership_expires_at',coalesce(v_current_expires,v_existing.ends_at)
    );
  end if;

  select max(ends_at) into v_current_expires
  from membership_entitlements
  where uid=actor_uid and tier='premium' and status='active'
    and ends_at>v_now;

  v_starts := coalesce(v_current_expires,v_now);
  v_ends := v_starts + interval '30 days';

  insert into membership_entitlements(
    id,uid,tier,starts_at,ends_at,status,source,provider_ref,
    idempotency_key,request_hash,amount_fen
  ) values (
    p_entitlement_id,actor_uid,'premium',v_starts,v_ends,'active',
    'demo_purchase',p_idempotency_key::text,
    p_idempotency_key,p_request_hash,p_amount_fen
  );

  return jsonb_build_object(
    'id',p_entitlement_id,'activated',true,'tier','premium',
    'membership_expires_at',v_ends
  );
end;
$$;

create or replace function create_ai_quote(
  actor_uid text, p_operation_id uuid, p_idempotency_key uuid,
  p_request_hash text, p_intent jsonb, p_messages jsonb, p_billing_policy jsonb,
  p_quoted_at timestamptz, p_quote_expires_at timestamptz,
  p_budget_valid_until timestamptz, p_quoted_input_token_upper_bound bigint,
  p_quoted_microcredits bigint
) returns jsonb language plpgsql security invoker as $$
declare
  v_action text := p_intent ->> 'action';
  v_article text := p_intent ->> 'article_key';
  v_commit text := p_intent ->> 'git_commit';
  v_segment uuid;
  v_parent bigint;
  v_replace bigint;
  v_output bigint;
  v_total bigint;
  v_scope text;
  v_active text;
  v_existing ai_operations%rowtype;
  v_cached ai_operations%rowtype;
  v_replace_node reading_nodes%rowtype;
  v_replace_action text;
begin
  perform _billing_assert_service_role();
  if not exists (
    select 1 from billing_settings where id=1 and billing_enabled
      and billing_rule='total_tokens_1_to_1' and text_model='deepseek-flash'
      and quota_timezone='Asia/Shanghai' and quota_reset_hour=4
      and standard_period_tokens=50000 and premium_period_tokens=500000
      and premium_monthly_price_fen=1990
  ) then raise exception using errcode='55000', message='BILLING_CONFIG_UNAVAILABLE'; end if;
  if nullif(btrim(actor_uid),'') is null or v_action not in ('init','explain','ask','advanced')
     or nullif(v_article,'') is null or nullif(v_commit,'') is null
     or nullif(p_request_hash,'') is null or jsonb_typeof(p_messages) <> 'object'
  then raise exception using errcode='22023', message='INVALID_INPUT'; end if;
  begin
    v_segment := (p_intent ->> 'segment_id')::uuid;
    v_parent := nullif(p_intent ->> 'parent_id','')::bigint;
    v_replace := nullif(p_intent ->> 'replace_node_id','')::bigint;
    v_output := (p_billing_policy ->> 'output_limit')::bigint;
  exception when others then
    raise exception using errcode='22023', message='INVALID_INPUT';
  end;
  if p_billing_policy ->> 'billing_rule' <> 'total_tokens_1_to_1'
     or p_billing_policy ->> 'microcredits_per_token' <> '1000000'
     or p_billing_policy ->> 'requested_model' <> 'deepseek-flash'
     or p_billing_policy -> 'allowed_returned_models' <> '["deepseek-flash"]'::jsonb
     or v_output <= 0 or p_quoted_input_token_upper_bound < 0
     or p_quoted_input_token_upper_bound > 9223372036854 - v_output
     or p_quote_expires_at <> p_quoted_at + interval '60 seconds'
     or p_budget_valid_until <> p_quote_expires_at + interval '300 seconds'
     or p_quote_expires_at <= clock_timestamp()
  then raise exception using errcode='22023', message='BILLING_CONFIG_UNAVAILABLE'; end if;
  v_total := p_quoted_input_token_upper_bound + v_output;
  if p_quoted_microcredits <> v_total * 1000000 then
    raise exception using errcode='22023', message='BILLING_CONTRACT_MISMATCH';
  end if;
  if _reading_segment_index(v_article, v_commit, v_segment) is null then
    raise exception using errcode='P0001', message='SEGMENT_NOT_FOUND';
  end if;
  if v_action='init' and (v_parent is not null or p_intent -> 'pos_start' <> 'null'::jsonb
                          or p_intent -> 'question' <> 'null'::jsonb) then
    raise exception using errcode='22023', message='INVALID_INPUT';
  elsif v_action='explain' and (
      jsonb_typeof(p_intent -> 'pos_start') <> 'number'
      or jsonb_typeof(p_intent -> 'pos_end') <> 'number'
      or (p_intent ->> 'pos_start')::bigint < 0
      or (p_intent ->> 'pos_start')::bigint >= (p_intent ->> 'pos_end')::bigint
      or p_intent -> 'question' <> 'null'::jsonb
    ) then raise exception using errcode='22023', message='INVALID_OFFSET';
  elsif v_action='ask' and nullif(btrim(p_intent ->> 'question'),'') is null then
    raise exception using errcode='22023', message='INVALID_INPUT';
  elsif v_action='advanced' and v_parent is null then
    raise exception using errcode='22023', message='INVALID_INPUT';
  end if;
  if v_parent is not null and not _reading_branch_visible(v_parent, actor_uid, v_article, v_segment, false) then
    raise exception using errcode='P0001', message='PARENT_NOT_FOUND';
  end if;
  if v_replace is not null and not _reading_branch_visible(v_replace, actor_uid, v_article, v_segment, false) then
    raise exception using errcode='P0001', message='NODE_NOT_FOUND';
  end if;
  if v_replace is not null then
    select n.* into v_replace_node from reading_nodes n where n.id=v_replace;
    select o.action into v_replace_action from ai_operations o
    where o.id=v_replace_node.operation_id;
    if v_replace_node.kind is distinct from
         (case when v_action='advanced' then 'explain' else v_action end)
       or v_replace_node.parent_id is distinct from v_parent
       or v_replace_node.pos_start is distinct from nullif(p_intent->>'pos_start','')::bigint
       or v_replace_node.pos_end is distinct from nullif(p_intent->>'pos_end','')::bigint
       or coalesce(v_replace_node.question,'') is distinct from coalesce(p_intent->>'question','')
       or (v_action='advanced' and v_replace_action is distinct from 'advanced') then
      raise exception using errcode='P0001',message='REGENERATION_INTENT_MISMATCH';
    end if;
  end if;
  if v_action='advanced' and not exists (
    select 1 from membership_entitlements e where e.uid=actor_uid and e.tier='premium'
      and e.status='active' and e.starts_at<=clock_timestamp() and e.ends_at>clock_timestamp()
  ) then raise exception using errcode='P0001', message='MEMBERSHIP_REQUIRED'; end if;

  v_scope := 'user:' || actor_uid;
  select * into v_existing from ai_operations
  where scope_key=v_scope and action=v_action and idempotency_key=p_idempotency_key;
  if found then
    if v_existing.request_hash <> p_request_hash then
      raise exception using errcode='23505', message='IDEMPOTENCY_CONFLICT';
    end if;
    return to_jsonb(v_existing);
  end if;
  v_active := _billing_active_key(v_action, p_intent, p_request_hash);
  if v_replace is null then
    select o.* into v_cached from ai_operations o
    join reading_nodes n on n.id=o.result_node_id
    where o.uid=actor_uid and o.action=v_action and o.active_key=v_active
      and o.status in ('settled','cache_hit')
      and _reading_branch_visible(n.id, actor_uid, v_article, v_segment, false)
    order by o.settled_at desc nulls last, o.created_at desc limit 1;
  end if;
  insert into ai_operations(
    id,uid,payer_scope,action,scope_key,idempotency_key,request_hash,effective_input_hash,
    active_key,article_key,git_commit,segment_id,parent_id,replace_node_id,intent_json,
    messages_json,status,created_at,quote_expires_at,budget_valid_until,
    quoted_input_token_upper_bound,output_limit_tokens,quoted_total_tokens,
    quoted_microcredits,billing_policy,requested_model,result_node_id
  ) values (
    p_operation_id,actor_uid,'user',v_action,v_scope,p_idempotency_key,p_request_hash,
    p_request_hash,v_active,v_article,v_commit,v_segment,v_parent,v_replace,p_intent,
    case when v_cached.id is null then p_messages else null end,
    case when v_cached.id is null then 'quoted' else 'cache_hit' end,
    p_quoted_at,p_quote_expires_at,p_budget_valid_until,p_quoted_input_token_upper_bound,
    v_output,v_total,p_quoted_microcredits,p_billing_policy,'deepseek-flash',v_cached.result_node_id
  ) returning * into v_existing;
  return to_jsonb(v_existing);
end;
$$;

create or replace function reserve_ai_operation(actor_uid text, p_operation_id uuid)
returns jsonb language plpgsql security invoker as $$
declare
  v_pre ai_operations%rowtype;
  v_op ai_operations%rowtype;
  v_daily daily_token_quotas%rowtype;
  v_wallet credit_wallets%rowtype;
  v_cached ai_operations%rowtype;
  v_now timestamptz := clock_timestamp();
  v_period date;
  v_tier text := 'standard';
  v_limit bigint := 50000;
  v_daily_part bigint;
  v_wallet_part bigint;
begin
  perform _billing_assert_service_role();
  select * into v_pre from ai_operations where id=p_operation_id and uid=actor_uid;
  if not found then raise exception using errcode='P0001', message='OPERATION_NOT_FOUND'; end if;
  if v_pre.status in ('reserved','dispatched','result_recorded','settled','cache_hit',
      'quote_expired','waived_not_dispatched','waived_failure','waived_unknown') then
    return to_jsonb(v_pre);
  end if;
  if v_pre.status <> 'quoted' then
    raise exception using errcode='P0001', message='OPERATION_NOT_EXECUTABLE';
  end if;

  perform 1 from membership_entitlements e
  where e.uid=actor_uid and e.tier='premium' and e.status='active'
    and e.starts_at<=v_now and e.ends_at>v_now
  order by e.ends_at desc,e.id limit 1 for update;
  if found then v_tier:='premium'; v_limit:=500000; end if;
  v_period:=quota_period_at(v_now);
  insert into daily_token_quotas(uid,quota_period,membership_tier,limit_tokens)
  values(actor_uid,v_period,v_tier,v_limit)
  on conflict(uid,quota_period) do update set
    membership_tier=case when excluded.limit_tokens>daily_token_quotas.limit_tokens
                         then excluded.membership_tier else daily_token_quotas.membership_tier end,
    limit_tokens=greatest(daily_token_quotas.limit_tokens,excluded.limit_tokens),updated_at=now();
  select * into v_daily from daily_token_quotas
  where uid=actor_uid and quota_period=v_period for update;
  insert into credit_wallets(uid) values(actor_uid) on conflict(uid) do nothing;
  select * into v_wallet from credit_wallets where uid=actor_uid for update;
  perform pg_advisory_xact_lock(hashtextextended(
    'reading-node|'||actor_uid||'|'||v_pre.article_key||'|'||v_pre.segment_id::text,0));
  select * into v_op from ai_operations where id=p_operation_id and uid=actor_uid for update;
  if v_op.status in ('reserved','dispatched','result_recorded','settled','cache_hit',
      'quote_expired','waived_not_dispatched','waived_failure','waived_unknown') then
    return to_jsonb(v_op);
  end if;
  if v_op.status <> 'quoted' then raise exception using errcode='P0001',message='OPERATION_NOT_EXECUTABLE'; end if;
  if v_op.quote_expires_at <= v_now then
    update ai_operations set status='quote_expired',error_code='QUOTE_EXPIRED'
    where id=v_op.id returning * into v_op;
    return to_jsonb(v_op);
  end if;
  if v_op.action='advanced' and v_tier<>'premium' then
    raise exception using errcode='P0001',message='MEMBERSHIP_REQUIRED';
  end if;
  if _reading_segment_index(v_op.article_key,v_op.git_commit,v_op.segment_id) is null then
    raise exception using errcode='P0001',message='SEGMENT_NOT_FOUND';
  end if;
  if v_op.parent_id is not null and not _reading_branch_visible(
       v_op.parent_id,actor_uid,v_op.article_key,v_op.segment_id,false) then
    raise exception using errcode='P0001',message='PARENT_CHANGED';
  end if;
  if v_op.replace_node_id is not null and not _reading_branch_visible(
       v_op.replace_node_id,actor_uid,v_op.article_key,v_op.segment_id,false) then
    raise exception using errcode='P0001',message='REPLACED_NODE_CHANGED';
  end if;

  if v_op.replace_node_id is null then
    select o.* into v_cached from ai_operations o join reading_nodes n on n.id=o.result_node_id
    where o.id<>v_op.id and o.uid=actor_uid and o.action=v_op.action
      and o.active_key=v_op.active_key and o.status in ('settled','cache_hit')
      and _reading_branch_visible(n.id,actor_uid,v_op.article_key,v_op.segment_id,false)
    order by o.settled_at desc nulls last,o.created_at desc limit 1;
    if found then
      update ai_operations set status='cache_hit',messages_json=null,
        result_node_id=v_cached.result_node_id
      where id=v_op.id returning * into v_op;
      return to_jsonb(v_op);
    end if;
  end if;
  if exists(select 1 from ai_operations o where o.id<>v_op.id and o.scope_key=v_op.scope_key
      and o.active_key=v_op.active_key and o.status in ('reserved','dispatched','result_recorded')) then
    raise exception using errcode='P0001',message='OPERATION_IN_PROGRESS';
  end if;
  if v_op.action='explain' and exists(
    select 1 from reading_nodes n
    where n.uid=actor_uid and n.article_key=v_op.article_key and n.segment_id=v_op.segment_id
      and n.kind='explain' and n.parent_id is not distinct from v_op.parent_id
      and n.id<>coalesce(v_op.replace_node_id,-1) and n.pos_start < (v_op.intent_json->>'pos_end')::bigint
      and n.pos_end > (v_op.intent_json->>'pos_start')::bigint
      and _reading_branch_visible(n.id,actor_uid,v_op.article_key,v_op.segment_id,false)
  ) then raise exception using errcode='P0001',message='OVERLAP'; end if;

  v_daily_part:=least(v_op.quoted_total_tokens,
    v_daily.limit_tokens-v_daily.used_tokens-v_daily.reserved_tokens);
  v_wallet_part:=(v_op.quoted_total_tokens-v_daily_part)*1000000;
  if v_wallet.balance_microcredits-v_wallet.ai_reserved_microcredits-
       v_wallet.refund_reserved_microcredits < v_wallet_part then
    raise exception using errcode='P0001',message='INSUFFICIENT_CREDITS';
  end if;
  update daily_token_quotas set reserved_tokens=reserved_tokens+v_daily_part,updated_at=now()
  where uid=actor_uid and quota_period=v_period returning * into v_daily;
  update credit_wallets set ai_reserved_microcredits=ai_reserved_microcredits+v_wallet_part,
    updated_at=now() where uid=actor_uid returning * into v_wallet;
  update ai_operations set status='reserved',quota_period=v_period,
    membership_tier=v_tier,quota_limit_tokens=v_daily.limit_tokens,
    reserved_daily_tokens=v_daily_part,reserved_wallet_microcredits=v_wallet_part,
    reserved_total_tokens=v_op.quoted_total_tokens,
    deadline_at=least(v_op.budget_valid_until,v_now+interval '60 seconds')
  where id=v_op.id returning * into v_op;
  insert into credit_ledger(
    uid,event_key,kind,ai_operation_id,quota_period,daily_reserved_delta,
    daily_used_after,daily_reserved_after,balance_after,ai_reserved_delta,
    ai_reserved_after,refund_reserved_after
  ) values(actor_uid,'ai:'||v_op.id||':reserve','ai_reserve',v_op.id,v_period,
    v_daily_part,v_daily.used_tokens,v_daily.reserved_tokens,v_wallet.balance_microcredits,
    v_wallet_part,v_wallet.ai_reserved_microcredits,v_wallet.refund_reserved_microcredits)
  on conflict(event_key) do nothing;
  return to_jsonb(v_op);
end;
$$;

create or replace function claim_ai_operation(p_executor_token uuid)
returns jsonb language plpgsql security invoker as $$
declare
  v_op ai_operations%rowtype;
  v_stale record;
begin
  perform _billing_assert_service_role();
  -- 每次领取前有界清理崩溃遗留任务。waive 函数按会员→原周期→钱包→
  -- 段锁→操作行的统一锁序释放，且 event_key 令并发清理幂等。
  for v_stale in
    select id,status from ai_operations
    where (status='reserved' and deadline_at<=clock_timestamp())
       or (status='dispatched' and deadline_at<=clock_timestamp())
    order by deadline_at,created_at limit 20
  loop
    perform waive_ai_operation(
      v_stale.id,
      case when v_stale.status='dispatched' then 'TIMEOUT_UNKNOWN'
           else 'QUEUE_TIMEOUT' end
    );
  end loop;
  select * into v_op from ai_operations
  where status='result_recorded'
  order by result_recorded_at,created_at limit 1 for update skip locked;
  if found then
    update ai_operations set executor_token=p_executor_token where id=v_op.id returning * into v_op;
    return to_jsonb(v_op);
  end if;
  select * into v_op from ai_operations
  where status='reserved' and deadline_at>clock_timestamp() and budget_valid_until>clock_timestamp()
  order by created_at limit 1 for update skip locked;
  if not found then return null; end if;
  update ai_operations set status='dispatched',executor_token=p_executor_token,
    dispatch_started_at=clock_timestamp(),
    deadline_at=least(budget_valid_until,clock_timestamp()+interval '240 seconds')
  where id=v_op.id returning * into v_op;
  return to_jsonb(v_op);
end;
$$;

create or replace function record_ai_result(
  p_operation_id uuid,p_executor_token uuid,p_result_json jsonb,p_result_hash text
) returns jsonb language plpgsql security invoker as $$
declare
  v_pre ai_operations%rowtype;
  v_op ai_operations%rowtype;
begin
  perform _billing_assert_service_role();
  select * into v_pre from ai_operations where id=p_operation_id;
  if not found then raise exception using errcode='P0001',message='OPERATION_NOT_FOUND'; end if;
  if v_pre.status='dispatched' and v_pre.executor_token is not distinct from p_executor_token
     and (p_result_hash !~ '^[0-9a-f]{64}$' or p_result_json is null) then
    return waive_ai_operation(p_operation_id,'BILLING_CONTRACT_MISMATCH');
  end if;
  if v_pre.status='dispatched' and v_pre.executor_token is not distinct from p_executor_token then
    v_pre.result_json:=p_result_json;
    if not _billing_result_is_valid(v_pre) then
      return waive_ai_operation(p_operation_id,'BILLING_CONTRACT_MISMATCH');
    end if;
  end if;
  select * into v_op from ai_operations where id=p_operation_id for update;
  if not found then raise exception using errcode='P0001',message='OPERATION_NOT_FOUND'; end if;
  if v_op.result_hash is not null then
    if v_op.result_hash<>p_result_hash then
      raise exception using errcode='P0001',message='BILLING_CONTRACT_MISMATCH';
    end if;
    return to_jsonb(v_op);
  end if;
  if v_op.status not in ('dispatched','waived_unknown')
     or (v_op.status='dispatched' and v_op.executor_token is distinct from p_executor_token) then
    raise exception using errcode='P0001',message='OPERATION_NOT_RECORDABLE';
  end if;
  v_op.result_json:=p_result_json;
  if not _billing_result_is_valid(v_op) then
    -- 正常 worker 已在无锁预检分支释放；这里只可能是并发篡改，回滚拒绝。
    raise exception using errcode='P0001',message='BILLING_CONTRACT_MISMATCH';
  end if;
  update ai_operations set
    result_json=p_result_json,result_hash=p_result_hash,
    returned_model=p_result_json->>'returned_model',
    provider_response_id=p_result_json->>'response_id',usage_json=p_result_json->'usage',
    usage_tokens=(p_result_json->'usage'->>'total_tokens')::bigint,
    result_recorded_at=clock_timestamp(),
    status=case when status='waived_unknown' then status else 'result_recorded' end
  where id=p_operation_id returning * into v_op;
  return to_jsonb(v_op);
end;
$$;

create or replace function finalize_ai_operation(p_operation_id uuid)
returns jsonb language plpgsql security invoker as $$
declare
  v_pre ai_operations%rowtype;
  v_op ai_operations%rowtype;
  v_daily daily_token_quotas%rowtype;
  v_wallet credit_wallets%rowtype;
  v_node reading_nodes%rowtype;
  v_total bigint;
  v_daily_charge bigint;
  v_wallet_charge bigint;
  v_seg_index integer;
  v_kind text;
  v_invalid text;
begin
  perform _billing_assert_service_role();
  select * into v_pre from ai_operations where id=p_operation_id;
  if not found then raise exception using errcode='P0001',message='OPERATION_NOT_FOUND'; end if;
  if v_pre.status in ('settled','cache_hit','quote_expired','waived_not_dispatched','waived_failure','waived_unknown')
  then return to_jsonb(v_pre); end if;
  if v_pre.status<>'result_recorded' then
    raise exception using errcode='P0001',message='OPERATION_NOT_SETTLEABLE';
  end if;
  perform 1 from membership_entitlements e where e.uid=v_pre.uid
    order by e.ends_at desc,e.id limit 1 for update;
  select * into v_daily from daily_token_quotas
    where uid=v_pre.uid and quota_period=v_pre.quota_period for update;
  select * into v_wallet from credit_wallets where uid=v_pre.uid for update;
  perform pg_advisory_xact_lock(hashtextextended(
    'reading-node|'||v_pre.uid||'|'||v_pre.article_key||'|'||v_pre.segment_id::text,0));
  select * into v_op from ai_operations where id=p_operation_id for update;
  if v_op.status='settled' then return to_jsonb(v_op); end if;
  if v_op.status<>'result_recorded' then return to_jsonb(v_op); end if;

  if not _billing_result_is_valid(v_op) then v_invalid:='BILLING_CONTRACT_MISMATCH'; end if;
  v_seg_index:=_reading_segment_index(v_op.article_key,v_op.git_commit,v_op.segment_id);
  if v_seg_index is null then v_invalid:=coalesce(v_invalid,'SEGMENT_NOT_FOUND'); end if;
  if v_op.parent_id is not null and not _reading_branch_visible(
       v_op.parent_id,v_op.uid,v_op.article_key,v_op.segment_id,false) then
    v_invalid:=coalesce(v_invalid,'PARENT_CHANGED');
  end if;
  if v_op.replace_node_id is not null and not _reading_branch_visible(
       v_op.replace_node_id,v_op.uid,v_op.article_key,v_op.segment_id,false) then
    v_invalid:=coalesce(v_invalid,'REPLACED_NODE_CHANGED');
  end if;
  if v_invalid is not null then
    update daily_token_quotas set reserved_tokens=reserved_tokens-v_op.reserved_daily_tokens,
      updated_at=now() where uid=v_op.uid and quota_period=v_op.quota_period returning * into v_daily;
    update credit_wallets set ai_reserved_microcredits=ai_reserved_microcredits-
      v_op.reserved_wallet_microcredits,updated_at=now() where uid=v_op.uid returning * into v_wallet;
    insert into credit_ledger(uid,event_key,kind,ai_operation_id,quota_period,
      daily_reserved_delta,daily_used_after,daily_reserved_after,balance_after,
      ai_reserved_delta,ai_reserved_after,refund_reserved_after)
    values(v_op.uid,'ai:'||v_op.id||':release','ai_release',v_op.id,v_op.quota_period,
      -v_op.reserved_daily_tokens,v_daily.used_tokens,v_daily.reserved_tokens,
      v_wallet.balance_microcredits,-v_op.reserved_wallet_microcredits,
      v_wallet.ai_reserved_microcredits,v_wallet.refund_reserved_microcredits)
    on conflict(event_key) do nothing;
    update ai_operations set status='waived_failure',error_code=v_invalid,messages_json=null
      where id=v_op.id returning * into v_op;
    return to_jsonb(v_op);
  end if;

  v_total:=v_op.usage_tokens;
  v_daily_charge:=least(v_total,v_op.reserved_daily_tokens);
  v_wallet_charge:=(v_total-v_daily_charge)*1000000;
  if v_wallet_charge>v_op.reserved_wallet_microcredits then
    raise exception using errcode='P0001',message='BILLING_CONTRACT_MISMATCH';
  end if;
  v_kind:=case when v_op.action='advanced' then 'explain' else v_op.action end;
  if v_op.replace_node_id is not null then
    update reading_nodes set is_current=false where id=v_op.replace_node_id
      and uid=v_op.uid and is_current and deleted_at is null;
    if not found then raise exception using errcode='P0001',message='REPLACED_NODE_CHANGED'; end if;
  end if;
  insert into reading_nodes(
    article_key,seg_index,kind,content,uid,parent_id,pos_start,pos_end,question,at,
    segment_id,generated_commit,operation_id,supersedes_id,is_current
  ) values(
    v_op.article_key,v_seg_index,v_kind,v_op.result_json->>'content',v_op.uid,
    v_op.parent_id,nullif(v_op.intent_json->>'pos_start','')::bigint,
    nullif(v_op.intent_json->>'pos_end','')::bigint,
    coalesce(v_op.intent_json->>'question',''),extract(epoch from clock_timestamp())::bigint,
    v_op.segment_id,v_op.git_commit,v_op.id,v_op.replace_node_id,true
  ) returning * into v_node;
  update daily_token_quotas set used_tokens=used_tokens+v_daily_charge,
    reserved_tokens=reserved_tokens-v_op.reserved_daily_tokens,updated_at=now()
  where uid=v_op.uid and quota_period=v_op.quota_period returning * into v_daily;
  update credit_wallets set balance_microcredits=balance_microcredits-v_wallet_charge,
    ai_reserved_microcredits=ai_reserved_microcredits-v_op.reserved_wallet_microcredits,
    updated_at=now() where uid=v_op.uid returning * into v_wallet;
  insert into credit_ledger(uid,event_key,kind,ai_operation_id,quota_period,
    daily_used_delta,daily_reserved_delta,daily_used_after,daily_reserved_after,
    balance_delta,ai_reserved_delta,balance_after,ai_reserved_after,refund_reserved_after)
  values(v_op.uid,'ai:'||v_op.id||':settle','ai_settle',v_op.id,v_op.quota_period,
    v_daily_charge,-v_op.reserved_daily_tokens,v_daily.used_tokens,v_daily.reserved_tokens,
    -v_wallet_charge,-v_op.reserved_wallet_microcredits,v_wallet.balance_microcredits,
    v_wallet.ai_reserved_microcredits,v_wallet.refund_reserved_microcredits)
  on conflict(event_key) do nothing;
  update ai_operations set status='settled',charged_daily_tokens=v_daily_charge,
    charged_wallet_microcredits=v_wallet_charge,result_node_id=v_node.id,
    settled_at=clock_timestamp(),messages_json=null
  where id=v_op.id returning * into v_op;
  return to_jsonb(v_op);
end;
$$;

create or replace function waive_ai_operation(p_operation_id uuid,p_reason text)
returns jsonb language plpgsql security invoker as $$
declare
  v_pre ai_operations%rowtype;
  v_op ai_operations%rowtype;
  v_daily daily_token_quotas%rowtype;
  v_wallet credit_wallets%rowtype;
  v_status text;
begin
  perform _billing_assert_service_role();
  select * into v_pre from ai_operations where id=p_operation_id;
  if not found then raise exception using errcode='P0001',message='OPERATION_NOT_FOUND'; end if;
  if v_pre.status in ('settled','cache_hit','quote_expired','waived_not_dispatched','waived_failure','waived_unknown')
  then return to_jsonb(v_pre); end if;
  perform 1 from membership_entitlements e where e.uid=v_pre.uid
    order by e.ends_at desc,e.id limit 1 for update;
  if v_pre.quota_period is not null then
    select * into v_daily from daily_token_quotas where uid=v_pre.uid
      and quota_period=v_pre.quota_period for update;
  end if;
  insert into credit_wallets(uid) values(v_pre.uid) on conflict(uid) do nothing;
  select * into v_wallet from credit_wallets where uid=v_pre.uid for update;
  perform pg_advisory_xact_lock(hashtextextended(
    'reading-node|'||v_pre.uid||'|'||v_pre.article_key||'|'||v_pre.segment_id::text,0));
  select * into v_op from ai_operations where id=p_operation_id for update;
  if v_op.status in ('settled','cache_hit','quote_expired','waived_not_dispatched','waived_failure','waived_unknown')
  then return to_jsonb(v_op); end if;
  if p_reason='BILLING_CONTRACT_MISMATCH' and v_op.status='result_recorded'
     and _billing_result_is_valid(v_op) then
    return to_jsonb(v_op);
  end if;
  if v_op.status='quoted' then
    v_status:=case when v_op.quote_expires_at<=clock_timestamp() then 'quote_expired'
                   else 'waived_not_dispatched' end;
  elsif v_op.status='reserved' then v_status:='waived_not_dispatched';
  elsif p_reason in ('TIMEOUT_UNKNOWN','UNKNOWN_OUTCOME') then v_status:='waived_unknown';
  else v_status:='waived_failure'; end if;
  if v_op.quota_period is not null then
    update daily_token_quotas set reserved_tokens=reserved_tokens-v_op.reserved_daily_tokens,
      updated_at=now() where uid=v_op.uid and quota_period=v_op.quota_period returning * into v_daily;
    update credit_wallets set ai_reserved_microcredits=ai_reserved_microcredits-
      v_op.reserved_wallet_microcredits,updated_at=now() where uid=v_op.uid returning * into v_wallet;
    insert into credit_ledger(uid,event_key,kind,ai_operation_id,quota_period,
      daily_reserved_delta,daily_used_after,daily_reserved_after,balance_after,
      ai_reserved_delta,ai_reserved_after,refund_reserved_after)
    values(v_op.uid,'ai:'||v_op.id||':release','ai_release',v_op.id,v_op.quota_period,
      -v_op.reserved_daily_tokens,v_daily.used_tokens,v_daily.reserved_tokens,
      v_wallet.balance_microcredits,-v_op.reserved_wallet_microcredits,
      v_wallet.ai_reserved_microcredits,v_wallet.refund_reserved_microcredits)
    on conflict(event_key) do nothing;
  end if;
  update ai_operations set status=v_status,error_code=left(coalesce(p_reason,'UNKNOWN'),80),
    messages_json=null where id=v_op.id returning * into v_op;
  return to_jsonb(v_op);
end;
$$;

create or replace function begin_content_update(
  actor_uid text,p_update_id uuid,p_idempotency_key uuid,p_article_key text,
  p_expected_commit text,p_request_hash text,p_candidate_json jsonb
) returns jsonb language plpgsql security invoker as $$
declare
  v_existing content_updates%rowtype;
  v_current text;
begin
  perform _billing_assert_service_role();
  if nullif(btrim(actor_uid),'') is null or nullif(btrim(p_article_key),'') is null
     or nullif(btrim(p_request_hash),'') is null
     or jsonb_typeof(p_candidate_json)<>'object'
     or jsonb_typeof(p_candidate_json->'source')<>'object' then
    raise exception using errcode='22023',message='INVALID_INPUT';
  end if;
  select * into v_existing from content_updates
    where uid=actor_uid and idempotency_key=p_idempotency_key for update;
  if found then
    if v_existing.request_hash<>p_request_hash then
      raise exception using errcode='23505',message='IDEMPOTENCY_CONFLICT';
    end if;
    return to_jsonb(v_existing);
  end if;
  perform pg_advisory_xact_lock(hashtextextended('content-publish|'||p_article_key,0));
  select current_commit into v_current from distilled where key=p_article_key for update;
  if v_current is distinct from p_expected_commit then
    raise exception using errcode='40001',message='VERSION_CONFLICT';
  end if;
  insert into content_updates(
    id,uid,article_key,idempotency_key,request_hash,expected_commit,candidate_json,
    state,deadline_at
  ) values(
    p_update_id,actor_uid,p_article_key,p_idempotency_key,p_request_hash,
    p_expected_commit,p_candidate_json,'queued',clock_timestamp()+interval '30 minutes'
  ) returning * into v_existing;
  return to_jsonb(v_existing);
end;
$$;

create or replace function claim_content_update(p_executor_token uuid)
returns jsonb language plpgsql security invoker as $$
declare v_update content_updates%rowtype;
begin
  perform _billing_assert_service_role();
  select * into v_update from content_updates
  where (state='git_saved' and (executor_token is null or lease_until<=clock_timestamp()))
     or state='queued'
     or (state='building' and lease_until<clock_timestamp() and deadline_at>clock_timestamp())
  order by case state when 'git_saved' then 0 when 'queued' then 1 else 2 end,created_at
  limit 1 for update skip locked;
  if not found then return null; end if;
  if v_update.state<>'git_saved' then
    update content_updates set state='building',executor_token=p_executor_token,
      lease_until=clock_timestamp()+interval '10 minutes',updated_at=now()
    where id=v_update.id returning * into v_update;
  else
    update content_updates set executor_token=p_executor_token,
      lease_until=clock_timestamp()+interval '2 minutes',updated_at=now()
    where id=v_update.id returning * into v_update;
  end if;
  return to_jsonb(v_update);
end;
$$;

create or replace function save_content_candidate(
  p_update_id uuid,p_executor_token uuid,p_candidate_json jsonb
) returns jsonb language plpgsql security invoker as $$
declare v_update content_updates%rowtype;
begin
  perform _billing_assert_service_role();
  if jsonb_typeof(p_candidate_json)<>'object'
     or jsonb_typeof(p_candidate_json->'source')<>'object'
     or jsonb_typeof(p_candidate_json->'manifest')<>'object'
     or jsonb_typeof(p_candidate_json->'manifest'->'segments')<>'array'
     or jsonb_typeof(p_candidate_json->'cuts')<>'array'
     or nullif(btrim(p_candidate_json->>'summary'),'') is null
     or nullif(btrim(p_candidate_json->>'manifest_sha256'),'') is null then
    raise exception using errcode='22023',message='INVALID_CONTENT_CANDIDATE';
  end if;
  select * into v_update from content_updates where id=p_update_id for update;
  if not found then raise exception using errcode='P0001',message='UPDATE_NOT_FOUND'; end if;
  if v_update.state='git_saved' or v_update.state='published' then return to_jsonb(v_update); end if;
  if v_update.state<>'building' or v_update.executor_token is distinct from p_executor_token
     or v_update.lease_until<=clock_timestamp() then
    raise exception using errcode='P0001',message='CONTENT_UPDATE_LEASE_LOST';
  end if;
  update content_updates set candidate_json=p_candidate_json,
    lease_until=clock_timestamp()+interval '10 minutes',updated_at=now()
  where id=p_update_id returning * into v_update;
  return to_jsonb(v_update);
end;
$$;

create or replace function mark_content_git_saved(
  p_update_id uuid,p_executor_token uuid,p_candidate_commit text
) returns jsonb language plpgsql security invoker as $$
declare v_update content_updates%rowtype;
begin
  perform _billing_assert_service_role();
  if p_candidate_commit !~ '^[0-9a-f]{40,64}$' then
    raise exception using errcode='22023',message='INVALID_GIT_COMMIT';
  end if;
  select * into v_update from content_updates where id=p_update_id for update;
  if not found then raise exception using errcode='P0001',message='UPDATE_NOT_FOUND'; end if;
  if v_update.state in ('git_saved','published') then
    if v_update.candidate_commit is distinct from p_candidate_commit then
      raise exception using errcode='P0001',message='GIT_CANDIDATE_CONFLICT';
    end if;
    return to_jsonb(v_update);
  end if;
  if v_update.state<>'building' or v_update.executor_token is distinct from p_executor_token
     or v_update.lease_until<=clock_timestamp()
     or jsonb_typeof(v_update.candidate_json->'manifest'->'segments')<>'array' then
    raise exception using errcode='P0001',message='CONTENT_UPDATE_NOT_GIT_READY';
  end if;
  update content_updates set state='git_saved',candidate_commit=p_candidate_commit,
    lease_until=clock_timestamp()+interval '2 minutes',updated_at=now()
  where id=p_update_id returning * into v_update;
  return to_jsonb(v_update);
end;
$$;

create or replace function publish_content_update(
  p_update_id uuid,p_executor_token uuid
) returns jsonb language plpgsql security invoker as $$
declare
  v_update content_updates%rowtype;
  v_current text;
  v_source jsonb;
  v_version reading_articles%rowtype;
begin
  perform _billing_assert_service_role();
  select * into v_update from content_updates where id=p_update_id;
  if not found then raise exception using errcode='P0001',message='UPDATE_NOT_FOUND'; end if;
  if v_update.state='published' then return to_jsonb(v_update); end if;
  perform pg_advisory_xact_lock(hashtextextended('content-publish|'||v_update.article_key,0));
  select * into v_update from content_updates where id=p_update_id for update;
  if v_update.state='published' then return to_jsonb(v_update); end if;
  if v_update.state<>'git_saved' or v_update.executor_token is distinct from p_executor_token
     or v_update.candidate_commit is null then
    raise exception using errcode='P0001',message='CONTENT_UPDATE_NOT_PUBLISHABLE';
  end if;
  select current_commit into v_current from distilled
    where key=v_update.article_key for update;
  if v_current is distinct from v_update.expected_commit then
    update content_updates set state='conflict',error_code='VERSION_CONFLICT',
      updated_at=now() where id=v_update.id returning * into v_update;
    return to_jsonb(v_update);
  end if;
  v_source:=v_update.candidate_json->'source';
  if nullif(btrim(v_source->>'url'),'') is null
     or nullif(btrim(v_source->>'title'),'') is null
     or nullif(btrim(v_source->>'content'),'') is null
     or jsonb_typeof(v_update.candidate_json->'manifest'->'segments')<>'array' then
    raise exception using errcode='22023',message='INVALID_CONTENT_CANDIDATE';
  end if;
  insert into reading_articles(
    article_key,git_commit,parent_commit,update_id,summary,cuts,segments,
    manifest_sha256,published_at,by_uid,at
  ) values(
    v_update.article_key,v_update.candidate_commit,v_update.expected_commit,v_update.id,
    v_update.candidate_json->>'summary',v_update.candidate_json->'cuts',
    v_update.candidate_json->'manifest',v_update.candidate_json->>'manifest_sha256',
    clock_timestamp(),v_update.uid,extract(epoch from clock_timestamp())::bigint
  ) on conflict(article_key,git_commit) do nothing;
  select * into v_version from reading_articles where article_key=v_update.article_key
    and git_commit=v_update.candidate_commit;
  if v_version.update_id is distinct from v_update.id
     or v_version.manifest_sha256 is distinct from v_update.candidate_json->>'manifest_sha256' then
    raise exception using errcode='P0001',message='PUBLISHED_VERSION_CONFLICT';
  end if;
  insert into distilled(key,title,url,content,images,length,at,current_commit,updated_by_uid)
  values(v_update.article_key,v_source->>'title',v_source->>'url',v_source->>'content',
    coalesce(v_source->'images','[]'::jsonb),length(v_source->>'content'),
    extract(epoch from clock_timestamp())::bigint,v_update.candidate_commit,v_update.uid)
  on conflict(key) do update set title=excluded.title,url=excluded.url,
    content=excluded.content,images=excluded.images,length=excluded.length,at=excluded.at,
    current_commit=excluded.current_commit,updated_by_uid=excluded.updated_by_uid;
  update content_updates set state='published',published_at=clock_timestamp(),
    updated_at=now(),lease_until=null,error_code=null
  where id=v_update.id returning * into v_update;
  return to_jsonb(v_update);
end;
$$;

create or replace function fail_content_update(
  p_update_id uuid,p_executor_token uuid,p_error_code text
) returns jsonb language plpgsql security invoker as $$
declare v_update content_updates%rowtype;
begin
  perform _billing_assert_service_role();
  select * into v_update from content_updates where id=p_update_id for update;
  if not found then raise exception using errcode='P0001',message='UPDATE_NOT_FOUND'; end if;
  if v_update.state in ('published','conflict','failed','git_saved') then
    return to_jsonb(v_update);
  end if;
  if v_update.state<>'building' or v_update.executor_token is distinct from p_executor_token then
    raise exception using errcode='P0001',message='CONTENT_UPDATE_LEASE_LOST';
  end if;
  update content_updates set state='failed',error_code=left(coalesce(p_error_code,'UNKNOWN'),80),
    lease_until=null,updated_at=now() where id=p_update_id returning * into v_update;
  return to_jsonb(v_update);
end;
$$;

create or replace function list_private_segment_nodes(
  actor_uid text,p_article_key text,p_segment_id uuid,p_include_history boolean default false
) returns setof reading_nodes language plpgsql security invoker as $$
begin
  perform _billing_assert_service_role();
  return query select n.* from reading_nodes n
  where n.uid=actor_uid and n.article_key=p_article_key and n.segment_id=p_segment_id
    and _reading_branch_visible(n.id,actor_uid,p_article_key,p_segment_id,p_include_history)
  order by n.id;
end;
$$;

create or replace function get_private_node(
  actor_uid text,p_node_id bigint,p_article_key text,p_segment_id uuid,
  p_include_history boolean default false
) returns jsonb language plpgsql security invoker as $$
declare v_node reading_nodes%rowtype;
begin
  perform _billing_assert_service_role();
  select * into v_node from reading_nodes where id=p_node_id and uid=actor_uid
    and article_key=p_article_key and segment_id=p_segment_id
    and _reading_branch_visible(id,actor_uid,p_article_key,p_segment_id,p_include_history);
  return case when found then to_jsonb(v_node) else null end;
end;
$$;

create or replace function delete_private_node_tree(
  actor_uid text,p_node_id bigint,p_article_key text,p_segment_id uuid
) returns jsonb language plpgsql security invoker as $$
declare v_count bigint;
begin
  perform _billing_assert_service_role();
  perform pg_advisory_xact_lock(hashtextextended(
    'reading-node|'||actor_uid||'|'||p_article_key||'|'||p_segment_id::text,0));
  if not _reading_branch_visible(p_node_id,actor_uid,p_article_key,p_segment_id,true) then
    return jsonb_build_object('deleted_count',0);
  end if;
  with recursive tree as (
    select id from reading_nodes where id=p_node_id and uid=actor_uid
      and article_key=p_article_key and segment_id=p_segment_id
    union all
    select n.id from reading_nodes n join tree t on n.parent_id=t.id
    where n.uid=actor_uid and n.article_key=p_article_key and n.segment_id=p_segment_id
  )
  update reading_nodes set deleted_at=clock_timestamp(),is_current=false
  where id in (select id from tree) and deleted_at is null;
  get diagnostics v_count=row_count;
  return jsonb_build_object('deleted_count',v_count);
end;
$$;

revoke all on billing_settings,membership_entitlements,daily_token_quotas,
  credit_wallets,ai_operations,credit_ledger,content_updates,
  reading_nodes from public;
revoke execute on function _billing_assert_service_role(),_billing_sha256(jsonb),
  _billing_active_key(text,jsonb,text),_reading_segment_index(text,text,uuid),
  _reading_branch_visible(bigint,text,text,uuid,boolean),
  _billing_result_is_valid(ai_operations) from public;
revoke execute on function apply_demo_membership(
  text,uuid,uuid,text,bigint
) from public;

do $$
begin
  if exists(select 1 from pg_roles where rolname='anon') then
    execute 'revoke all on billing_settings,membership_entitlements,daily_token_quotas,credit_wallets,ai_operations,credit_ledger,content_updates,reading_nodes from anon';
    execute 'revoke execute on function apply_demo_membership(text,uuid,uuid,text,bigint) from anon';
  end if;
  if exists(select 1 from pg_roles where rolname='authenticated') then
    execute 'revoke all on billing_settings,membership_entitlements,daily_token_quotas,credit_wallets,ai_operations,credit_ledger,content_updates,reading_nodes from authenticated';
    execute 'revoke execute on function apply_demo_membership(text,uuid,uuid,text,bigint) from authenticated';
  end if;
  if exists(select 1 from pg_roles where rolname='service_role') then
    execute 'grant select,insert,update on billing_settings,membership_entitlements,daily_token_quotas,credit_wallets,ai_operations,credit_ledger,content_updates,reading_nodes,reading_articles,distilled to service_role';
    execute 'grant usage,select on all sequences in schema public to service_role';
    execute 'grant execute on function get_billing_account(text),apply_demo_membership(text,uuid,uuid,text,bigint),create_ai_quote(text,uuid,uuid,text,jsonb,jsonb,jsonb,timestamptz,timestamptz,timestamptz,bigint,bigint),reserve_ai_operation(text,uuid),claim_ai_operation(uuid),record_ai_result(uuid,uuid,jsonb,text),finalize_ai_operation(uuid),waive_ai_operation(uuid,text),begin_content_update(text,uuid,uuid,text,text,text,jsonb),claim_content_update(uuid),save_content_candidate(uuid,uuid,jsonb),mark_content_git_saved(uuid,uuid,text),publish_content_update(uuid,uuid),fail_content_update(uuid,uuid,text),list_private_segment_nodes(text,text,uuid,boolean),get_private_node(text,bigint,text,uuid,boolean),delete_private_node_tree(text,bigint,text,uuid) to service_role';

  end if;
end;
$$;

commit;
