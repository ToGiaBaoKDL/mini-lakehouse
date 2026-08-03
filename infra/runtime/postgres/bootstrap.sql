SELECT format('CREATE ROLE airflow LOGIN PASSWORD %L', :'airflow_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'airflow') \gexec

SELECT format('ALTER ROLE airflow WITH LOGIN CONNECTION LIMIT 20 PASSWORD %L', :'airflow_password') \gexec

SELECT 'CREATE DATABASE airflow OWNER airflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow') \gexec

REVOKE CONNECT ON DATABASE postgres FROM airflow;

\connect airflow
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO airflow;
