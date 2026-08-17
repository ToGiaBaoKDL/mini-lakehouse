SELECT format('CREATE ROLE pg_monitor_user LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pg_monitor_user') \gexec

SELECT format('ALTER ROLE pg_monitor_user WITH LOGIN CONNECTION LIMIT 2 PASSWORD %L', :'application_password') \gexec

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO pg_monitor_user;

GRANT pg_monitor TO pg_monitor_user;
