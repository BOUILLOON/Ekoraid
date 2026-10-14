-- Extension additive après cloud/supabase.sql. Réexécutable, aucune catégorie précréée.
-- Auth e-mail confirmé + whitelist privée ; ne jamais ajouter une clé service_role au site.
begin;

create table if not exists public.playlist25_categories (
  id text primary key default replace(gen_random_uuid()::text,'-','') check(id ~ '^[0-9a-f]{32}$'),
  name text not null check(char_length(name) between 1 and 80 and name=btrim(name) and name !~ '[[:cntrl:]]'),
  sort_order integer not null default 0 check(sort_order>=0),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists playlist25_categories_active_name on public.playlist25_categories(lower(name)) where active;
alter table public.playlist25_proposals add column if not exists category_ids jsonb not null default '[]'::jsonb
  check(jsonb_typeof(category_ids)='array');
create table if not exists public.playlist25_organizer_emails (
  email text primary key check(email=lower(btrim(email)) and char_length(email)<=254
    and email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  active boolean not null default true,
  created_at timestamptz not null default now()
);
alter table public.playlist25_categories enable row level security;
alter table public.playlist25_organizer_emails enable row level security;
revoke all on table public.playlist25_categories,public.playlist25_organizer_emails from public,anon,authenticated;
grant select,insert,update on table public.playlist25_categories,public.playlist25_organizer_emails to service_role;

create or replace function public.playlist25_organizer_id() returns uuid
language sql stable security definer set search_path=pg_catalog,public
as $$ select u.id from auth.users u join public.playlist25_organizer_emails e on e.email=lower(u.email) and e.active
  where u.id=auth.uid() and u.email_confirmed_at is not null and u.deleted_at is null
    and (u.banned_until is null or u.banned_until<=now()) $$;

create or replace function public.playlist25_approve_organizer(p_email text,p_active boolean default true) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
begin
  if p_email is null or char_length(btrim(p_email))>254 or btrim(p_email) !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' or p_active is null then
    return jsonb_build_object('error','Adresse e-mail invalide.','status',422);
  end if;
  insert into public.playlist25_organizer_emails(email,active) values(lower(btrim(p_email)),p_active)
    on conflict(email) do update set active=excluded.active;
  return jsonb_build_object('approved',p_active);
end $$;

create or replace function public.playlist25_organizer_session() returns jsonb
language sql stable security definer set search_path=pg_catalog,public
as $$ select jsonb_build_object('authenticated',public.playlist25_organizer_id() is not null,
  'organizer',(select jsonb_build_object('id',u.id,'email',u.email) from auth.users u where u.id=public.playlist25_organizer_id())) $$;

create or replace function public.playlist25_categories_catalogue(p_all boolean default false) returns jsonb
language sql stable security definer set search_path=pg_catalog,public
as $$ select coalesce(jsonb_agg(to_jsonb(c) order by c.sort_order,c.name,c.id),'[]'::jsonb)
  from public.playlist25_categories c where p_all or c.active $$;

create or replace function public.playlist25_categories_list() returns jsonb
language sql stable security definer set search_path=pg_catalog,public
as $$ select jsonb_build_object('categories',public.playlist25_categories_catalogue(true)) $$;

-- Les suggestions restent privées et ne créent aucune ligne dans le catalogue.
create or replace function public.playlist25_category_suggestions() returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
begin
  if public.playlist25_organizer_id() is null then return jsonb_build_object('error','Connexion organisation autorisée nécessaire.','status',403); end if;
  return jsonb_build_object('items',jsonb_build_array(
    jsonb_build_object('name','Accueil','reason','Pour accueillir les invités.'),
    jsonb_build_object('name','Repas','reason','Pour accompagner le repas.'),
    jsonb_build_object('name','Danse','reason','Pour les moments sur la piste.'),
    jsonb_build_object('name','Souvenirs','reason','Pour les morceaux liés à Fanny et à ses proches.'),
    jsonb_build_object('name','Fin de soirée','reason','Pour les derniers morceaux.')));
end $$;

create or replace function public.playlist25_category_ids_valid(p_ids jsonb,p_archived boolean default false,p_overrides jsonb default '[]',p_previous jsonb default '[]') returns boolean
language plpgsql stable security definer set search_path=pg_catalog,public
as $$
declare value jsonb; cid text; category_active boolean;
begin
  if jsonb_typeof(p_ids) is distinct from 'array' then return false; end if;
  if exists(select 1 from jsonb_array_elements(p_ids) x group by x having count(*)>1) then return false; end if;
  for value in select * from jsonb_array_elements(p_ids) loop
    if jsonb_typeof(value) is distinct from 'string' then return false; end if;
    cid:=value#>>'{}';
    if cid !~ '^[0-9a-f]{32}$' then return false; end if;
    select (e->>'active')::boolean into category_active from jsonb_array_elements(p_overrides) e where e->>'id'=cid;
    if not found then select active into category_active from public.playlist25_categories where id=cid; end if;
    if category_active is null or (not p_archived and not category_active and not p_previous ? cid) then return false; end if;
  end loop;
  return true;
end $$;

-- Lot explicite depuis le PC ; catégories et choix validés ensemble avant écriture.
create or replace function public.playlist25_admin_categories(p_categories jsonb default '[]',p_assignments jsonb default '[]') returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
declare entry jsonb; key text; nc integer:=0; na integer:=0; previous jsonb;
begin
  if jsonb_typeof(p_categories) is distinct from 'array' or jsonb_typeof(p_assignments) is distinct from 'array' then
    return jsonb_build_object('error','Lot de catégories invalide.','status',422);
  end if;
  if jsonb_array_length(p_categories)>10000 or jsonb_array_length(p_assignments)>10000 then
    return jsonb_build_object('error','Lot de catégories trop volumineux.','status',422); end if;
  perform pg_advisory_xact_lock(hashtext('playlist25/categories'));
  for entry in select * from jsonb_array_elements(p_categories) loop
    if jsonb_typeof(entry) is distinct from 'object' or not entry ?& array['id','name','sort_order','active'] then
      return jsonb_build_object('error','Catégorie incomplète.','status',422); end if;
    for key in select jsonb_object_keys(entry) loop
      if key not in ('id','name','sort_order','active') then return jsonb_build_object('error','Champ de catégorie non autorisé.','status',422); end if;
    end loop;
    if jsonb_typeof(entry->'id') is distinct from 'string' or entry->>'id' !~ '^[0-9a-f]{32}$'
      or jsonb_typeof(entry->'name') is distinct from 'string' or char_length(btrim(entry->>'name')) not between 1 and 80
      or entry->>'name' ~ '[[:cntrl:]]' or jsonb_typeof(entry->'active') is distinct from 'boolean'
      or jsonb_typeof(entry->'sort_order') is distinct from 'number' or entry->>'sort_order' !~ '^[0-9]{1,10}$'
      or (entry->>'sort_order')::numeric>2147483647 then
      return jsonb_build_object('error','Catégorie invalide.','status',422); end if;
  end loop;
  if exists(select 1 from jsonb_array_elements(p_categories) e group by e->>'id' having count(*)>1) then
    return jsonb_build_object('error','Catégorie répétée.','status',422); end if;
  if exists(select 1 from (
    select lower(btrim(e->>'name')) name from jsonb_array_elements(p_categories) e where (e->>'active')::boolean
    union all select lower(c.name) from public.playlist25_categories c where c.active
      and not exists(select 1 from jsonb_array_elements(p_categories) e where e->>'id'=c.id)
  ) names group by name having count(*)>1) then return jsonb_build_object('error','Ce nom de catégorie existe déjà.','status',409); end if;
  for entry in select * from jsonb_array_elements(p_assignments) loop
    if jsonb_typeof(entry) is distinct from 'object' or not entry ?& array['id','category_ids'] then
      return jsonb_build_object('error','Choix de catégories incomplet.','status',422); end if;
    for key in select jsonb_object_keys(entry) loop
      if key not in ('id','category_ids') then return jsonb_build_object('error','Champ de choix non autorisé.','status',422); end if;
    end loop;
    if jsonb_typeof(entry->'id') is distinct from 'number' or entry->>'id' !~ '^[1-9][0-9]{0,17}$' then
      return jsonb_build_object('error','Choix de catégories invalide.','status',422); end if;
    select category_ids into previous from public.playlist25_proposals where id=(entry->>'id')::bigint;
    if not found then
      return jsonb_build_object('error','Proposition introuvable.','status',404); end if;
    if not public.playlist25_category_ids_valid(entry->'category_ids',false,p_categories,previous) then
      return jsonb_build_object('error','Une catégorie archivée peut seulement être conservée sur sa proposition.','status',422); end if;
  end loop;
  if exists(select 1 from jsonb_array_elements(p_assignments) e group by e->>'id' having count(*)>1) then
    return jsonb_build_object('error','Proposition répétée.','status',422); end if;
  -- Autorise échanges de noms/archives dans le même lot sans conflit transitoire.
  update public.playlist25_categories c set active=false where exists(select 1 from jsonb_array_elements(p_categories) e where e->>'id'=c.id);
  for entry in select * from jsonb_array_elements(p_categories) loop
    insert into public.playlist25_categories(id,name,sort_order,active) values(entry->>'id',btrim(entry->>'name'),(entry->>'sort_order')::integer,(entry->>'active')::boolean)
      on conflict(id) do update set name=excluded.name,sort_order=excluded.sort_order,active=excluded.active,updated_at=now();
    nc:=nc+1;
  end loop;
  for entry in select * from jsonb_array_elements(p_assignments) loop
    update public.playlist25_proposals set category_ids=entry->'category_ids' where id=(entry->>'id')::bigint;
    na:=na+1;
  end loop;
  return jsonb_build_object('updated_categories',nc,'updated_assignments',na);
end $$;

create or replace function public.playlist25_organizer_category(p_category jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
declare uid uuid:=public.playlist25_organizer_id(); cid text; current_category public.playlist25_categories; entry jsonb; result jsonb; key text;
begin
  if uid is null then return jsonb_build_object('error','Connexion organisation autorisée nécessaire.','status',403); end if;
  if not public.playlist25_allow(public.playlist25_hash(uid::text),'organizer_categories',60,600) then return jsonb_build_object('error','Trop de modifications. Patientez une minute.','status',429); end if;
  if jsonb_typeof(p_category) is distinct from 'object' then return jsonb_build_object('error','Catégorie invalide.','status',422); end if;
  for key in select jsonb_object_keys(p_category) loop
    if key not in ('id','name','sort_order','active') then return jsonb_build_object('error','Champ de catégorie non autorisé.','status',422); end if;
  end loop;
  perform pg_advisory_xact_lock(hashtext('playlist25/categories'));
  if p_category ? 'id' then
    if jsonb_typeof(p_category->'id') is distinct from 'string' or p_category->>'id' !~ '^[0-9a-f]{32}$' then return jsonb_build_object('error','Catégorie invalide.','status',422); end if;
    cid:=p_category->>'id';
    select * into current_category from public.playlist25_categories where id=cid;
    if not found then return jsonb_build_object('error','Catégorie introuvable.','status',404); end if;
    entry:=jsonb_build_object('id',cid,'name',current_category.name,'sort_order',current_category.sort_order,'active',current_category.active)||p_category;
  else
    cid:=replace(gen_random_uuid()::text,'-','');
    entry:=jsonb_build_object('id',cid,'sort_order',least(coalesce((select max(sort_order)::bigint+1 from public.playlist25_categories),0),2147483647),'active',true)||p_category;
  end if;
  result:=public.playlist25_admin_categories(jsonb_build_array(entry),'[]');
  if result ? 'error' then return result; end if;
  return jsonb_build_object('item',(select to_jsonb(c) from public.playlist25_categories c where id=cid),'categories',public.playlist25_categories_catalogue(true));
end $$;

create or replace function public.playlist25_proposal_categories(p_token text,p_proposal_id bigint,p_category_ids jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
declare gid uuid; previous jsonb;
begin
  if not public.playlist25_allow(p_token,'proposal_categories',30,600) then return jsonb_build_object('error','Trop de modifications. Patientez une minute.','status',429); end if;
  gid:=public.playlist25_guest_id(p_token);
  if gid is null then return jsonb_build_object('error','Choisissez d’abord un pseudo.','status',401); end if;
  perform pg_advisory_xact_lock(hashtext('playlist25/categories'));
  select category_ids into previous from public.playlist25_proposals where id=p_proposal_id and guest_id=gid and state in ('pending','approved') and withdrawn_at is null for update;
  if not found then return jsonb_build_object('error','Proposition introuvable.','status',404); end if;
  if not public.playlist25_category_ids_valid(p_category_ids,false,'[]',previous) then return jsonb_build_object('error','Choisissez des catégories actives ou conservez les catégories déjà attribuées.','status',422); end if;
  update public.playlist25_proposals set category_ids=p_category_ids where id=p_proposal_id;
  return jsonb_build_object('item',public.playlist25_public_item(p_proposal_id,gid));
end $$;

drop function if exists public.playlist25_organizer_proposal_categories(bigint,jsonb);
create or replace function public.playlist25_organizer_proposal_categories(p_proposal_id bigint,p_category_ids jsonb,p_token text default null) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
declare uid uuid:=public.playlist25_organizer_id(); result jsonb;
begin
  if uid is null then return jsonb_build_object('error','Connexion organisation autorisée nécessaire.','status',403); end if;
  if not public.playlist25_allow(public.playlist25_hash(uid::text),'organizer_categories',60,600) then return jsonb_build_object('error','Trop de modifications. Patientez une minute.','status',429); end if;
  result:=public.playlist25_admin_categories('[]',jsonb_build_array(jsonb_build_object('id',p_proposal_id,'category_ids',p_category_ids)));
  if result ? 'error' then return result; end if;
  return jsonb_build_object('item',public.playlist25_public_item(p_proposal_id,public.playlist25_guest_id(p_token)));
end $$;

create or replace function public.playlist25_public_item(p_id bigint,p_guest uuid) returns jsonb
language sql stable security definer set search_path=pg_catalog,public
as $$ select jsonb_build_object('id',p.id,'source_id',s.id,'nickname',g.nickname,
  'state',p.state,'sort_order',p.sort_order,'title',s.title,'artist',s.artist,
  'channel',s.channel,'duration',s.duration,'thumbnail',s.thumbnail,'url',s.url,
  'video_id',s.video_id,'status','pending','created_at',p.created_at,'category_ids',p.category_ids,
  'source_kind',case when s.video_id is not null then 'video' when s.url is not null then 'playlist' else 'manual' end,
  'votes',(select count(*) from public.playlist25_votes v where v.proposal_id=p.id),
  'voted',exists(select 1 from public.playlist25_votes v where v.proposal_id=p.id and v.guest_id=p_guest),
  'mine',coalesce(p.guest_id=p_guest,false),'avatar_id',g.id,'avatar_version',g.avatar_version)
  from public.playlist25_proposals p join public.playlist25_sources s on s.id=p.source_id
  join public.playlist25_guests g on g.id=p.guest_id where p.id=p_id $$;

create or replace function public.playlist25_list(p_token text,p_query text default '',p_sort text default 'popular',p_mine boolean default false) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public
as $$
declare gid uuid; items jsonb;
begin
  if not public.playlist25_allow(p_token,'read',120,6000) then return jsonb_build_object('error','Trop de requêtes. Réessayez dans une minute.','status',429); end if;
  if char_length(coalesce(p_query,''))>150 then return jsonb_build_object('error','Recherche limitée à 150 caractères.','status',422); end if;
  gid:=public.playlist25_guest_id(p_token);
  select coalesce(jsonb_agg(item order by rank desc,created_at desc,id desc),'[]'::jsonb) into items from (
    select public.playlist25_public_item(p.id,gid) item,p.id,p.created_at,
      case when p_sort='recent' then 0 else (select count(*) from public.playlist25_votes v where v.proposal_id=p.id) end rank
    from public.playlist25_proposals p join public.playlist25_sources s on s.id=p.source_id
    where p.state in ('pending','approved') and p.withdrawn_at is null and (not p_mine or p.guest_id=gid)
      and (coalesce(p_query,'')='' or position(lower(p_query) in lower(s.title||' '||s.artist||' '||s.channel))>0)
  ) listing;
  return jsonb_build_object('items',items,'categories',public.playlist25_categories_catalogue(true));
end $$;

create or replace function public.playlist25_snapshot() returns jsonb
language sql stable security definer set search_path=pg_catalog,public
as $$ select jsonb_build_object('schema_version',1,'exported_at',now(),
  'settings',public.playlist25_public_settings(),'categories',public.playlist25_categories_catalogue(true),
  'guests',(select coalesce(jsonb_agg(jsonb_build_object('id',id,'nickname',nickname,'created_at',created_at,'avatar_data',avatar_data,'avatar_version',avatar_version)),'[]'::jsonb) from public.playlist25_guests),
  'sources',(select coalesce(jsonb_agg(to_jsonb(s)),'[]'::jsonb) from public.playlist25_sources s),
  'proposals',(select coalesce(jsonb_agg(to_jsonb(p)),'[]'::jsonb) from public.playlist25_proposals p),
  'votes',(select coalesce(jsonb_agg(to_jsonb(v)),'[]'::jsonb) from public.playlist25_votes v)) $$;

revoke all on function public.playlist25_organizer_id(),public.playlist25_approve_organizer(text,boolean),
  public.playlist25_organizer_session(),public.playlist25_categories_catalogue(boolean),public.playlist25_categories_list(),
  public.playlist25_category_suggestions(),public.playlist25_category_ids_valid(jsonb,boolean,jsonb,jsonb),
  public.playlist25_admin_categories(jsonb,jsonb),public.playlist25_organizer_category(jsonb),
  public.playlist25_proposal_categories(text,bigint,jsonb),public.playlist25_organizer_proposal_categories(bigint,jsonb,text)
  from public,anon,authenticated;
grant execute on function public.playlist25_organizer_session(),public.playlist25_categories_list(),
  public.playlist25_proposal_categories(text,bigint,jsonb) to anon,authenticated;
grant execute on function public.playlist25_category_suggestions(),public.playlist25_organizer_category(jsonb),
  public.playlist25_organizer_proposal_categories(bigint,jsonb,text) to authenticated;
grant execute on function public.playlist25_approve_organizer(text,boolean),public.playlist25_admin_categories(jsonb,jsonb) to service_role;
notify pgrst,'reload schema';
commit;
