# Phase 08 release preflight — Supabase grants/RLS, deployment sequencing, rollback runbook

Date: 2026-11-04 · **Final-release update (owner-authorized):** the table lock-down proposed in section A was implemented
inside the two never-deployed migrations (ADR-019) and verified on disposable PostgreSQL 15.19 and 17.11 (section E).
The advisory-lock code (ADR-018, `migrations/env.py`) is unchanged. Nothing in this document was executed against
Supabase or any production system. Sections A–D keep the original review (the A10 proposals P1 / P2 are superseded by
the migrations, see the status table); section E records the final staging results. This is the release handoff
reference; at the time of writing the owner still had to take the backup and click Manual Deploy. **Closure update:** the release
then happened, and its outcome (including that the backup was NOT taken) is recorded in section F; sections 0 to E are kept as
the pre-release record.

Labels used below: **CONFIRMED** (read from the code, run in this session, or supplied by the owner) ·
**ASSUMPTION** (reasonable but not verified here) · **UNRESOLVED** (needs an owner decision or owner-run check).

## 0. Release blockers and decisions — status after the final-release work
| # | Item | Status |
|---|---|---|
| B1 | New tables would be born with `arwdDxtm` for `anon` / `authenticated` / `service_role`. | **RESOLVED in code (ADR-019):** migrations 0005 and 0006 enable RLS (no policies) and revoke all privileges from the three roles in the same transaction as `CREATE TABLE`; roles that do not exist are skipped. Verified in staging (section E). Global default privileges are deliberately untouched. |
| B2 | Role used by the app / migrations. | **CONFIRMED by the owner:** the existing public application tables are owned by `postgres`. Staging proves owner access with a non-superuser, non-BYPASSRLS owner. Q3 / Q4 remain available as a cross-check. |
| B3 | State of the EXISTING tables. | **CONFIRMED by the owner:** RLS is enabled on all 14 currently listed public tables (including `alembic_version`), `force_rls` false. Their grants and policies are intentionally not changed by this release; existing API-role grants still appear in privilege checks and RLS is what protects them. The earlier "potentially critical" concern is therefore not confirmed. |
| B4 | Backup. | **NOT TAKEN — the owner explicitly waived the pre-deployment backup** (section F). It was OPEN at the time this table was written. |
| D1 | One combined release of 0005 + 0006. | Recorded as the release path (section B, ADR-017 amendment, ADR-018). |
| N1 | Nightly CI `llm-eval` receives `secrets.DATABASE_URL` (`.github/workflows/ci.yml:83`); eval modules never call `init_db()` / Alembic. | LOW / owner to confirm the secret is not production. |
| N2 | The push also releases Phases 00–07 (no migrations beyond 0004). Old uploads / pasted lectures must be re-uploaded (ADR-007). | Awareness. |

## A. Supabase security preflight

### A1. What the code does (CONFIRMED)
- `migrations/versions/0005_study_plans.py:30` and `0006_evaluation_attempts.py:34` call `op.create_table(...)` with **no
  `schema=`**. Tables land in the connection's default schema (ASSUMPTION: `public`; Q4 confirms where the existing
  tables live, and the new ones follow).
- **No GRANT, REVOKE, RLS, POLICY or `search_path`** appears in any migration (0001–0006), `migrations/env.py` or
  `alembic.ini` (grep, 2026-11-04). Ownership and privileges are therefore whatever the connecting role and the
  database defaults produce.
- One URL drives everything: `settings.database_url` (`src/core/config.py:166`) from the `DATABASE_URL` environment
  variable → `get_database_url()` (`src/core/db.py:24-37`) → the SQLAlchemy engine, Alembic (`migrations/env.py`,
  the Docker `CMD` and `init_db()`) and the LangGraph pool (`src/graph/memory.py`). There is **no `SET ROLE`**, no
  per-request role switching and no second connection string anywhere in `src`, `migrations` or `scripts`.
- The app accesses the database **only through direct SQLAlchemy / psycopg**. A repository-wide search (excluding
  `.env*`) finds **no** Supabase client, no `service_role`, no `anon` key, no `/rest/v1` call and no `SUPABASE_*`
  setting in `src`, `frontend`, `scripts`, `tests`, `evals`, `.github`, `pyproject.toml` or `package.json`. The word
  "Supabase" appears only in comments (`src/core/db.py:4,44`, `src/graph/memory.py:16,29,43`, `render.yaml`).
- `src/graph/memory.py` creates LangGraph tables at runtime (`store.setup()`, checkpointer setup) as the same role, in
  the same schema. They get the same default privileges as any other table.

### A2. Which role do migrations use? (B2: not recorded in the repository; now CONFIRMED by the owner as `postgres`-owned tables)
The role is the user part of `DATABASE_URL`. I did not and must not read it, and the repository does not record it.
**ASSUMPTION:** `postgres`, or a pooler alias such as `postgres.<project-ref>` that maps to the `postgres` role.
Confirm with read-only queries, no credentials involved:
- Q3 shows the role names of the app's **currently live** connections (the running production app is connected right
  now).
- Q4 shows who **owns** the existing tables, which is the role that ran migrations 0001–0004.
If both say `postgres`, the new tables will be created and owned by `postgres`.

### A3. Owner evidence (CONFIRMED by the owner, Supabase SQL Editor, PostgreSQL 17.11)
`pg_default_acl` for the public schema has two entries:
- creator `postgres`: `anon`, `authenticated`, `service_role` have `arwdDxtm` on **tables**;
- creator `supabase_admin`: the same.
No RLS row exists yet for `study_plans` / `evaluation_attempts` because they are not deployed.
**Consequence:** a table created by `postgres` in `public` is born with full privileges for the three API roles.
`arwdDxtm` = INSERT, SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN. Row-level security is the only
remaining barrier, and it is off by default.

### A4. The `supabase_admin` restriction (CONFIRMED from Supabase's own documentation)
- Changing `supabase_admin`'s default privileges fails: `42501: permission denied to change default privileges`; on
  Postgres 15+ the message is `must be member of role "supabase_admin"`. The project's `postgres` role is not a member
  of `supabase_admin`. **Do not attempt it.**
- It is also unnecessary. Supabase states that tables **you** create get the default privileges of **`postgres`**; the
  `supabase_admin` defaults apply only to objects that `supabase_admin` itself creates (for example extensions
  installed in `public`). Our tables are created by the app's role, not by `supabase_admin`.
- Supabase's supported fix is `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM
  anon, authenticated, service_role` (and the same for sequences / functions). It only affects objects created
  **after** it runs: "existing objects keep their grants". If another role creates objects (a migration tool's role),
  the defaults for **that** role must be changed too. After the change the API roles cannot reach a new table until it
  is granted explicitly.
- Residual: nothing in the available sources says whether `supabase_admin` ever creates objects in `public` during
  platform upgrades. If it did, such objects would carry its defaults. That is not our tables. It is a Supabase support
  question (community threads point to a support ticket). **UNRESOLVED, not a blocker for this release.**
- Sources: [Supabase KB — can't change the default privileges of supabase_admin](https://supabase.com/kb/troubleshooting/cant-change-the-default-privileges-of-supabase_admin-in-the-public-schema),
  [Supabase docs — Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security),
  [community thread 48063](https://github.com/orgs/supabase/discussions/48063).

### A5. Does any workflow need `service_role`? (CONFIRMED from the code; external use is an ASSUMPTION)
- **No.** None of the eight workflows, the background jobs, the MCP server, the scripts, the evals or CI uses a Supabase
  client, the Data API, the `service_role` key or the `anon` key (A1). Every database access is a direct SQLAlchemy /
  psycopg connection as the connection-string role.
- `service_role` has `BYPASSRLS` and exists to serve Data API requests that carry the secret key. Revoking its table
  privileges on `study_plans` / `evaluation_attempts` therefore cannot affect any application workflow.
- **ASSUMPTION / UNRESOLVED:** nothing outside the repository (Supabase Edge Functions, dashboards or scripts that use
  the service key, other applications on the same project) reads these tables. Both tables are new, so nothing external
  can depend on them yet. A default-privileges change does affect **future** tables created by `postgres`, including
  ones made in the dashboard Table Editor; the owner should confirm no other project on this database relies on
  automatic API access.

### A6. Minimal secure configuration for the two backend-only tables (RECOMMENDATION)
The application is the only legitimate client and it connects directly as the table owner. Therefore:
1. **No grants to `anon`, `authenticated` or `service_role`** on `study_plans` or `evaluation_attempts`.
2. **RLS enabled, with no policies.** For the API roles this is deny-all. It is the second layer if a grant ever appears
   (for example from a future default-privileges change by `supabase_admin`).
3. **The owner role keeps working untouched.** In Supabase the owner `postgres` has the `BYPASSRLS` attribute (the RLS
   guide says so), so enabling RLS without policies does not affect the backend. Do **not** use `FORCE ROW LEVEL
   SECURITY` (unnecessary, and it would also bind the owner in other setups).
4. **Prevent the problem for future tables** with the Supabase-documented `ALTER DEFAULT PRIVILEGES FOR ROLE postgres`
   (P1). Run it **before** the deploy so the new tables are never born with API grants. That also removes the
   exposure window between "tables created" and "tables locked down".
5. Needs no sequences (ids are `String(32)` UUID hex) and no `GRANT` for the app role (it owns the tables).
6. **Conditional:** if Q3/Q4 show that the app does **not** connect as the table owner (a custom role without
   `BYPASSRLS`), then RLS-without-policies would block the app. Use instead the owner-less variant in P4. Do not apply P2
   until B2 is answered.

### A7. Mechanism options (decided: option 2 variant — the lock-down lives in 0005 / 0006 themselves, ADR-019)
| Option | What | Pros | Cons |
|---|---|---|---|
| 1. Manual SQL (recommended minimum) | P1 before the deploy; P2 + verification right after the migration commits | No repository change; no re-staging; the tables are never born with API grants once P1 is in place | Relies on the owner running two statements; not reproducible in other environments |
| 2. New migration `0007` (follow-up) | A guarded `DO` block that revokes / enables RLS when the Supabase roles exist (`pg_roles` check, no-op on plain PostgreSQL and SQLite) | Atomic with 0005/0006 (one transaction), repeatable on every environment; matches Supabase's "grants and RLS in the same migration" advice | New schema work: needs spec + ADR, owner approval and a full staging re-run (both PostgreSQL versions) before the first deploy |
| 3. Edit 0005 / 0006 in place | Add the statements to the existing files | None over option 2 | Invalidates the staging evidence for migrations that have never run in production; not recommended |

Recommendation: do option 1 for this release. Consider option 2 as follow-up hardening (it fits Phase 09 "Operations").

### A8. Existing tables (B3 — answered by the owner: RLS enabled on all 14 public tables; original analysis kept below)
The same default privileges applied when the existing tables (`users`, `conversations`, `messages`, `feedback`,
`password_reset_tokens`, `email_verification_tokens`, `refresh_tokens`, `jobs`) and the runtime-created LangGraph
tables were created. This review cannot tell whether they are exposed. **It depends on two things only the
Supabase project can answer:** (a) is the Data API enabled and is `public` in its exposed schemas (Project Settings →
Data API; Q8 may also show it), and (b) is RLS enabled on those tables (Q4 `rowsecurity`)? If (a) is yes and (b) is no
for `users`, a request with the project's public `anon` key could read or modify it. Run Q4, Q5 and Q8. If exposure is
confirmed, treat it as a security incident independent of this release: the remedy is P5 (per table), reviewed
table by table. It is safe for the backend for the same reason as above.

### A9. Read-only inspection SQL (safe to run in the Supabase SQL Editor; EXECUTED: none by Claude)
```sql
-- Q1 version, current role
select version();
select current_user, session_user;

-- Q2 role attributes (superuser / BYPASSRLS)
select rolname, rolsuper, rolbypassrls, rolcanlogin
from pg_roles
where rolname in ('postgres','anon','authenticated','service_role','authenticator','supabase_admin')
order by 1;

-- Q3 roles of the app's LIVE connections (role names only; this identifies the migration/app role)
select usename, application_name, count(*) as connections
from pg_stat_activity
where datname = current_database() and usename is not null
group by 1, 2 order by 3 desc;

-- Q4 existing tables: schema, OWNER (= the role that ran migrations 0001-0004) and RLS flag
select schemaname, tablename, tableowner, rowsecurity
from pg_tables
where schemaname = 'public'
order by tablename;

-- Q5 effective table privileges per grantee (includes the owner default when relacl is NULL)
select c.relname, coalesce(r.rolname, 'PUBLIC') as grantee, a.privilege_type
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
left join pg_roles r on r.oid = a.grantee
where n.nspname = 'public' and c.relkind = 'r'
  and coalesce(r.rolname, 'PUBLIC') in ('anon','authenticated','service_role','PUBLIC')
order by 1, 2, 3;

-- Q6 default privileges (already run by the owner)
select pg_get_userbyid(defaclrole) as creator, defaclnamespace::regnamespace as schema,
       defaclobjtype as objtype, defaclacl
from pg_default_acl order by 1, 2, 3;

-- Q7 RLS policies
select schemaname, tablename, policyname, roles, cmd from pg_policies where schemaname = 'public';

-- Q8 Data API exposure hints (the dashboard setting is authoritative)
select rolname, rolconfig from pg_roles where rolname = 'authenticator';

-- Q9 current Alembic revision (expected before the release: 0004_single_active_session)
select version_num from alembic_version;
```

### A10. Original proposed SQL — **NOT EXECUTED.** P1 and P2 are SUPERSEDED by migrations 0005 / 0006 (ADR-019); P3–P5 remain reference only
```sql
-- P1 (BEFORE the deploy; once). Supabase-documented. Affects tables created by `postgres` AFTER it runs.
-- Existing tables keep their grants. Only applies to creator role `postgres` (never touches supabase_admin).
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated, service_role;

-- P2 (AFTER migration 0006 is applied; defense in depth; run in the same maintenance window).
-- Use only if B2 confirms the app connects as the table owner (`postgres`).
begin;
alter table public.study_plans         enable row level security;
alter table public.evaluation_attempts enable row level security;
revoke all on table public.study_plans         from anon, authenticated, service_role;
revoke all on table public.evaluation_attempts from anon, authenticated, service_role;
commit;

-- P3 (undo P1 only if it must be reverted; restores the Supabase default for future tables)
alter default privileges for role postgres in schema public
  grant all on tables to anon, authenticated, service_role;

-- P4 (ONLY if the app role is NOT the owner and lacks BYPASSRLS; replace app_role)
grant select, insert, update, delete on public.study_plans, public.evaluation_attempts to app_role;
create policy app_all on public.study_plans         for all to app_role using (true) with check (true);
create policy app_all on public.evaluation_attempts for all to app_role using (true) with check (true);

-- P5 (existing tables, only if Q4/Q5/Q8 show exposure; one table at a time, after review)
-- alter table public.<table> enable row level security;
-- revoke all on table public.<table> from anon, authenticated, service_role;
```
Post-change verification (read-only; run after P2):
```sql
select tablename, rowsecurity from pg_tables
where schemaname = 'public' and tablename in ('study_plans','evaluation_attempts');   -- both true
-- re-run Q5: no rows for anon / authenticated / service_role on the two tables
begin; set local role anon;          select count(*) from public.study_plans;         rollback;  -- expect 42501
begin; set local role authenticated; select count(*) from public.evaluation_attempts; rollback;  -- expect 42501
```
Then run the persistence smoke checks (`docs/SMOKE_CHECKLIST.md`, "Phase 08 persistence checks"): the backend must still
read and write both tables.

## B. Deployment sequencing — one combined release (RECOMMENDED)
**CONFIRMED facts**
- The Docker `CMD` runs `alembic upgrade head && uvicorn …` (`Dockerfile:74-76`, no `ENTRYPOINT`), and
  `init_db()` runs `alembic upgrade head` again at startup (`src/api/main.py:87`, `src/core/db.py:95`). **An image that
  contains `0006_evaluation_attempts.py` applies migration 0006 during startup**, immediately after 0005 when the
  database is at 0004. There is no pre-deploy step and no flag that holds a migration back. Render's Docker Command is
  blank, so the image `CMD` is what runs (owner-confirmed).
- The chain is linear: `0004_single_active_session → 0005_study_plans → 0006_evaluation_attempts`. Both new migrations
  are additive (one table each, no change to existing data). 0006 depends only on `users`, not on 0005.
- On PostgreSQL all pending revisions run in one transaction (`migrations/env.py`, no `transaction_per_migration`).
  Disposable-PostgreSQL staging (15.19 and 17.11) ran the full chain, downgrade / re-upgrade and 20 concurrent-start
  rounds successfully (ADR-018).
- ADR-017 asked for 0005 and 0006 to be deployed separately. That cannot be achieved with one image (see above) unless a
  commit containing only 0005 were built and deployed first. Such an intermediate commit was **never tested on its
  own**: the full suite (`1765 passed, 2 skipped`) and the PostgreSQL staging both ran on the combined tree.

**Why combined is recommended (no code evidence of a blocker)**
- It is the exact state that was tested; splitting adds an untested artifact.
- The risk that motivated separation (blast radius) is small: two additive tables, old code unaffected.
- Atomicity: ASSUMPTION based on PostgreSQL transactional DDL — if either migration failed, both would roll back and the
  database would stay at 0004. Staging did not inject a failure to prove it.
- One backup, one maintenance window, one smoke run.

**Sequence**
1. Section A decisions made; B2 and B3 queries run.
2. Backup (section C).
3. P1 (default-privileges change), owner.
4. Manual deploy of the release commit (Auto-Deploy is Off). Watch the logs for the Alembic lines
   `Running upgrade 0004… -> 0005…` and `0005… -> 0006…`, `Store: Postgres`, and a healthy `/health`.
5. P2 and the post-change verification SQL (section A10), owner.
6. Smoke tests for all eight workflows plus the "Phase 08 persistence checks".
Git commit grouping (3 commits) is only about history. The deploy is a single push of the finished tree.

## C. Rollback runbook (documentation only; nothing here was performed)

### C1. Facts
- **An older image cannot boot against a newer revision (CONFIRMED by a scratch probe in this session, SQLite, outside
  the repo).** After the new code upgraded a database to `0006_evaluation_attempts`, running `alembic upgrade head` from
  a migrations directory that lacks that file failed with
  `CommandError: Can't locate revision identified by '0006_evaluation_attempts'`. In production that is the first
  command of the image `CMD`; because of `&&`, uvicorn never starts and the container exits. This is Alembic
  script-directory behaviour (dialect independent); not re-run against Supabase.
  **Consequence:** Render's dashboard "Rollback" (re-deploy of the previous image) will **fail** once the database is at
  0005/0006.
- **Downgrade is destructive (CONFIRMED in staging).** `alembic downgrade 0004_single_active_session` drops
  `study_plans` and `evaluation_attempts`, so every saved plan, tick and evaluation attempt created since the release is
  lost; `users` and other tables are untouched. `downgrade 0005_study_plans` drops only `evaluation_attempts`. The
  tables' RLS / grants disappear with them and must be re-applied after a later re-upgrade (P2).
- The tables are additive: older application code ignores them (migration docstrings). The only problem is that the
  older image refuses to start.

### C2. Decision order — prefer forward-fix
1. **A migration fails during the deploy.** ASSUMPTION: Render keeps serving the previous instance until the new one is
   healthy, and PostgreSQL's transactional DDL leaves the database at 0004. Fix the cause and redeploy. No data action.
2. **Migrations succeeded but the application is defective (preferred: forward-fix).** Deploy a corrected commit. If the
   feature code must be backed out, **revert application code only and keep `0005_study_plans.py` and
   `0006_evaluation_attempts.py` in the tree**. That image knows revision 0006, boots, and ignores the tables. No
   database change, no data loss.
3. **A pre-release image must run (last resort).** The database must first be brought below the newer revision:
   - **3a. Destructive downgrade** (steps below), or
   - **3b. `alembic stamp 0004_single_active_session`** (non-destructive: changes only `alembic_version`, tables and
     data remain). **UNVERIFIED — not tested in staging; test it there first.** Before the new code is ever deployed
     again the owner **must** run `alembic stamp 0006_evaluation_attempts`, otherwise `CREATE TABLE` fails with "already
     exists" at boot.

### C3. Exact safe order for 3a (owner only; production credentials never go into chat or the repo)
1. Announce maintenance. **Stop the service** (Render: Suspend) so that no container runs `alembic upgrade head` while
   the database is downgraded (a booting container would immediately re-apply 0005/0006) and nothing writes during the
   change.
2. **Backup:** a full `pg_dump` of the database (or at least the `public` schema), then a data-only dump of the two
   tables to keep what users created: `pg_dump --data-only -t public.study_plans -t public.evaluation_attempts`.
   Backup availability depends on the Supabase plan (ASSUMPTION: free projects may have no dashboard backups, so rely
   on your own `pg_dump`).
3. From a checkout of the **release commit** (it contains the 0005/0006 files; an older checkout hits the same
   `CommandError`), with `DATABASE_URL` set only in that shell, using a direct or session-mode connection:
   `alembic current` → expect `0006_evaluation_attempts`.
4. `alembic downgrade 0005_study_plans` (drops `evaluation_attempts`) or `alembic downgrade 0004_single_active_session`
   (drops both). The command takes the advisory lock and runs in one transaction.
5. `alembic current` → expect the target revision; `select to_regclass('public.study_plans'),
   to_regclass('public.evaluation_attempts');` → both NULL for a downgrade to 0004.
6. Manually deploy the previous commit. Its `alembic upgrade head` is now a no-op at 0004 and the app boots.
7. Resume the service, check `/health`, run the eight-workflow smoke.
8. To roll forward later: deploy the release commit again (migrations re-create the tables), re-apply P2, optionally
   restore the exported rows from the data-only dump, then repeat the persistence smoke checks.

## D. Staging evidence and what remains unverified
- CONFIRMED: PostgreSQL 15.19 and 17.11 disposable staging, 85 checks passed (ADR-018). The production version is
  17.11 (owner-confirmed).
- UNVERIFIED: Supabase pooler mode, Render's deploy-overlap behaviour, failure-injection rollback of the migration
  transaction, the `alembic stamp` option, a real production-data restore, the live smoke of the eight workflows, and the
  migration running against the real Supabase (only disposable PostgreSQL 15.19 / 17.11 were used; section E).

## E. Final staging results (2026-11-04, disposable PostgreSQL; production never touched)
Environment: Docker 29.9.2; images `postgres:15.19-alpine` and `postgres:17.11-alpine` (production is 17.11); an
`--internal` Docker network with no route out; tmpfs data; no published port; build context without `.env`; random
throwaway credentials; the app image built from the production `Dockerfile`. The application and Alembic connect as a
**non-superuser, non-BYPASSRLS table owner** (`app_owner`), so owner access is not masked by superuser rights. The runner
script lives in the session scratchpad, not in the repository.

**Result: 236 checks passed, 0 failed** (116 per PostgreSQL version plus 4 shared build checks). Each item below passed on
both versions:
- **A. Ordinary PostgreSQL (`anon` / `authenticated` / `service_role` absent):** `alembic upgrade head` succeeds, RLS is
  enabled on both new tables, downgrade to 0004 and re-upgrade work.
- **B. Supabase-like cluster** (the three roles present, `USAGE` on the schema, default privileges granting them all new
  tables of the owner role, as in the production `pg_default_acl` evidence):
  - chain 0004 → 0005 → 0006; unique constraint, cascading foreign keys, both indexes, `timestamptz`, `double precision`
    as intended;
  - both new tables: owned by the app role, **RLS enabled and not forced, no policies, no privileges left for
    `anon` / `authenticated` / `service_role`**, owner privileges kept;
  - `anon`, `authenticated` and `service_role` each get `permission denied for table …` on SELECT and DELETE of both
    tables;
  - with rows present, even a temporary `SELECT` grant to `anon` shows **0 rows** (RLS is an independent second layer);
  - the non-superuser owner reads and writes both tables with RLS on, and the real application code (plan upsert replacing
    in place with `created_at` kept, attempt writes / reads including an unscored attempt, the progress analytics) runs
    against PostgreSQL;
  - **existing tables unchanged:** the signature of all their grants, RLS flags and policies is identical before 0005,
    after 0006, after downgrade and after re-upgrade, and the global `pg_default_acl` is identical (the baseline was
    verified to be a real hash, not an empty value);
  - user deletion cascades to both tables and keeps other users' rows; downgrade drops both tables, keeps users, and the
    re-upgrade re-applies the lock-down;
  - `init_db()` at head is a no-op (one `alembic_version` row, revision unchanged).
- **Advisory lock intact:** a runner blocked on key `80080601` while another session held it (observed waiting in
  `pg_locks`, schema still 0004) and completed after release. **3 concurrent runners × 10 rounds per version from
  0004: 20 / 20 rounds had every runner exit 0** (checked per container and per PID), the final revision
  `0006_evaluation_attempts`, RLS on both new tables and no API-role privileges.
- Mentor `PostgresStore` persists a profile across containers.
- Control experiment (not a gate): with the previous `env.py` (no lock), 6 / 10 rounds failed on 15.19 and 4 / 10 on 17.11
  with `CREATE TABLE study_plans` collisions.
- Offline unit tests (no database): `tests/test_migrations.py` generates the PostgreSQL SQL and asserts RLS is enabled
  right after each `CREATE TABLE`, the role-existence guard, no `FORCE`, no `CREATE POLICY`, no `ALTER DEFAULT
  PRIVILEGES`, no touching existing tables, and that SQLite executes none of it.

**Not covered by staging:** Supabase itself (pooler mode, its role attributes), a restore of real production data,
Render's behaviour on a failed deploy, and the live smoke of the eight workflows. The first production start is
therefore the first run of the migrations on Supabase: take the backup first (section C) and run the SQL checks below.

**Post-deploy SQL checks (read-only, owner, Supabase SQL Editor):**
```sql
select version_num from alembic_version;                       -- expect 0006_evaluation_attempts
select tablename, tableowner, rowsecurity from pg_tables
 where schemaname = 'public' and tablename in ('study_plans','evaluation_attempts');   -- owner postgres, rowsecurity true
select c.relname, coalesce(r.rolname,'PUBLIC') as grantee, a.privilege_type
  from pg_class c cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
  left join pg_roles r on r.oid = a.grantee
 where c.oid in ('public.study_plans'::regclass, 'public.evaluation_attempts'::regclass)
   and coalesce(r.rolname,'PUBLIC') in ('anon','authenticated','service_role','PUBLIC');  -- expect no rows
select tablename, policyname from pg_policies
 where schemaname = 'public' and tablename in ('study_plans','evaluation_attempts');      -- expect no rows
```

## F. Final production release and closure (2026-11-04)
This section records what happened after the release commit. Three kinds of evidence are kept apart. Nothing in the
production tier was verified by Claude: it comes from the owner's reports and SQL results, and no production system was
accessed during this documentation. Deployment timestamps and per-workflow smoke results were not recorded.

**1. Git (verified from the repository)**
- Release commit `f355d43` — "feat: complete Phase 08 product persistence and release hardening" (46 files).
- Follow-up fix `497e682` — "fix: reject symlink escapes in retrieval benchmark" (`scripts/bench_retrieval.py`,
  `tests/test_bench_retrieval.py`).
- Local `HEAD` = remote `origin/main` = `497e682af8f1dbb626d4311fe3472088df4d6567`; working tree clean at closure.

**2. Automated CI (owner-reported GitHub Actions results)**
- `f355d43`: failed — `1778 passed, 1 skipped, 1 failed`. The failure was
  `tests/test_bench_retrieval.py::test_mentor_kb_rejects_a_linked_directory_escape`. Cause: on Linux / Python 3.13
  `Path.rglob` no longer expands `**` through a symlinked directory, so `_corpus_hashes` silently skipped a linked folder
  instead of rejecting it (the file was never read, so no disclosure; it is Phase 06 benchmark tooling, not Phase 08 code
  and not the application runtime). Fixed in `497e682`; the test itself was not weakened.
- `497e682`: **3 successful checks and 2 skipped checks.** Successful and named: Frontend build, Offline quality gate; the
  third successful check is not named in the available evidence. **Skipped, NOT passed: the live LLM-as-judge
  faithfulness gate and the live deploy smoke test.** They are evidence of nothing.

**3. Isolated staging (disposable PostgreSQL, not Supabase)** — see section E: 15.19 and 17.11, 236 checks passed, 0
failed; focused migration tests `17 passed`; focused set `269 passed`; ruff, compileall and `git diff --check` clean. The
last full local suite run was `1765 passed, 2 skipped` (before the release hardening). For `497e682` the local check was
`tests/test_bench_retrieval.py` 54 passed in a Linux / Python 3.13 container (52 passed, 2 skipped on Windows).

**4. Production (owner-reported / owner-confirmed)**
| Item | Status |
|---|---|
| Vercel deployment (frontend) | Completed (owner-reported). |
| Render deployment (backend) | Completed (owner-reported). The deployed commit SHA is **not recorded** in the available evidence: owner-reported / unconfirmed. |
| Alembic revision on production Supabase | **`0006_evaluation_attempts`** (owner-confirmed with the SQL check from section E). |
| `study_plans` | RLS enabled (owner-confirmed). |
| `evaluation_attempts` | RLS enabled (owner-confirmed). |
| SELECT as `anon`, `authenticated`, `service_role` on both tables | Denied (owner-confirmed). |
| Existing public application tables | RLS reported enabled (owner-reported); their grants were deliberately left unchanged. |
| Eight-workflow production smoke test | Reported passed (owner-reported); individual results and the date were not recorded. |
| **Pre-deployment backup** | **NOT TAKEN — owner waived.** There is no restore point that predates migrations 0005 / 0006. |

**Limitations that remain visible**
- Rollback is forward-fix only; a downgrade would destroy `study_plans` / `evaluation_attempts` data and there is no backup
  to restore from (section C still applies).
- No production p50 / p95, real-user metrics or live LLM quality were measured.
- The two skipped CI jobs have not been shown to pass for this release.
- Existing public tables keep their API-role grants; RLS protects them from `anon` / `authenticated`, while `service_role`
  bypasses RLS (Supabase documentation). This is an owner decision and was not changed.
- Not recorded: whether the Data API exposes the `public` schema, what the nightly CI `DATABASE_URL` secret points at, and
  whether any concurrent container start occurred (so whether the advisory lock acted in production is unknown).

**Closure:** Phase 08 is OFFICIALLY CLOSED / COMPLETE. Phase 09 is NOT STARTED and needs the owner's explicit authorization.
