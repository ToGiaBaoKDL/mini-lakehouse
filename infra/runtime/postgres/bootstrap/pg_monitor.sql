-- Role names starting with pg_ are reserved by PostgreSQL, so the
-- monitoring login is called lakehouse_monitor.
SELECT format('CREATE ROLE lakehouse_monitor LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lakehouse_monitor') \gexec

-- The OpenTelemetry PostgreSQL receiver owns one bounded pool per database and
-- runs several statistics queries concurrently. Three databases with
-- max_open=4 require a role-wide ceiling of 12 connections.
SELECT format('ALTER ROLE lakehouse_monitor WITH LOGIN CONNECTION LIMIT 12 PASSWORD %L', :'application_password') \gexec

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO lakehouse_monitor;

GRANT pg_monitor TO lakehouse_monitor;
