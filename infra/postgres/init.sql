SELECT 'CREATE DATABASE polaris'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'polaris')\gexec

SELECT 'CREATE DATABASE prefect'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prefect')\gexec
