-- Роль и база, которых ждёт режим solo, заводятся один раз руками.
--
-- Запускается через Start.bat setup-db, который передаёт :role, :db и :pw и
-- подключается суперпользователем PostgreSQL. Всё, что дальше — таблицы, колонки,
-- указатели, — принадлежит миграциям в migrations\ и никогда этому файлу.
--
-- Идемпотентно нарочно: повторный запуск на уже существующей базе не меняет ничего,
-- поэтому за ним безопасно тянуться при сомнении. Учтите, что пароль уже
-- существующей роли он тоже не трогает; если POSTGRES_PASSWORD в .env потом сменили,
-- смените его и в PostgreSQL (ALTER ROLE vellar PASSWORD).

\set ON_ERROR_STOP on

-- CREATEDB нужен не игре — она не заводит баз никогда, — а scripts/backup.ps1, который
-- доказывает, что копия разворачивается, разворачивая её в отдельную базу и удаляя ту
-- снова. Копия, которую никто ни разу не распаковал, копией не является.
SELECT format('CREATE ROLE %I LOGIN CREATEDB PASSWORD %L', :'role', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')
\gexec

-- Существующие установки заведены без него; выдать его повторно ничего не меняет.
SELECT format('ALTER ROLE %I CREATEDB', :'role')
\gexec

-- Принадлежит этой же роли, поэтому миграциям не нужно выдавать ничего.
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db')
\gexec

\echo 'Role and database are in place.'
