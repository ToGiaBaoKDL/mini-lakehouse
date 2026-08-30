SELECT format('CREATE ROLE t0_trading LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 't0_trading') \gexec

SELECT format('ALTER ROLE t0_trading WITH LOGIN CONNECTION LIMIT 20 PASSWORD %L', :'application_password') \gexec

SELECT 'CREATE DATABASE t0_trading OWNER t0_trading'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 't0_trading') \gexec

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE t0_trading FROM PUBLIC;
GRANT CONNECT ON DATABASE t0_trading TO t0_trading;

SELECT 'GRANT CONNECT ON DATABASE t0_trading TO lakehouse_monitor'
WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'lakehouse_monitor') \gexec

\connect t0_trading

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO t0_trading;
