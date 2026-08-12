SELECT format('CREATE ROLE lightdash LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lightdash') \gexec

SELECT format('ALTER ROLE lightdash WITH LOGIN CONNECTION LIMIT 20 PASSWORD %L', :'application_password') \gexec

SELECT 'CREATE DATABASE lightdash OWNER lightdash'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lightdash') \gexec

REVOKE CONNECT ON DATABASE postgres FROM lightdash;

\connect lightdash

GRANT USAGE, CREATE ON SCHEMA public TO lightdash;
