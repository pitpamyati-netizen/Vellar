-- The role and the database that solo mode expects, created once by hand.
--
-- Run through Start.bat setup-db, which passes :role, :db and :pw and connects
-- as the PostgreSQL superuser. Everything after that - tables, columns, indexes
-- - belongs to the migrations in migrations\ and never to this file.
--
-- Idempotent on purpose: running it again on a database that already exists
-- changes nothing, so it is safe to reach for when unsure. Note that it also
-- leaves an existing role's password alone; if POSTGRES_PASSWORD in .env is
-- changed afterwards, change it in PostgreSQL too (ALTER ROLE vellar PASSWORD).

\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')
\gexec

-- Owned by that role, so the migrations need nothing granted to them.
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db')
\gexec

\echo 'Role and database are in place.'
