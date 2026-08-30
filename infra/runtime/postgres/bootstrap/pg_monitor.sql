-- Role names starting with pg_ are reserved by PostgreSQL, so the
-- monitoring login is called lakehouse_monitor.
SELECT format('CREATE ROLE lakehouse_monitor LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lakehouse_monitor') \gexec

-- Keep a bounded ceiling for the metrics collectors during the Netdata/SigNoz
-- acceptance overlap. The steady-state Netdata collector uses one small pool.
SELECT format('ALTER ROLE lakehouse_monitor WITH LOGIN CONNECTION LIMIT 16 PASSWORD %L', :'application_password') \gexec

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO lakehouse_monitor;

-- Collectors inspect only these owned databases. Grant only CONNECT;
-- pg_monitor supplies their read-only statistics privileges.
SELECT format('GRANT CONNECT ON DATABASE %I TO lakehouse_monitor', datname)
FROM pg_database
WHERE datname IN ('airflow', 'lightdash', 't0_trading') \gexec

GRANT pg_monitor TO lakehouse_monitor;

-- Native Top Queries uses the bounded statement store. Query texts remain in
-- PostgreSQL and are exposed by Netdata only through its sensitive Function.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT 1 FROM pg_stat_statements LIMIT 0;
