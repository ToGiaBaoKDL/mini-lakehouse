-- Role names starting with pg_ are reserved by PostgreSQL, so the
-- monitoring login is called lakehouse_monitor.
SELECT format('CREATE ROLE lakehouse_monitor LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lakehouse_monitor') \gexec

SELECT format('ALTER ROLE lakehouse_monitor WITH LOGIN CONNECTION LIMIT 2 PASSWORD %L', :'application_password') \gexec

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO lakehouse_monitor;

GRANT pg_monitor TO lakehouse_monitor;
