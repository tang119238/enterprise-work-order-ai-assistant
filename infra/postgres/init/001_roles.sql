\getenv flyway_owner_password FLYWAY_PASSWORD
\getenv work_order_app_password WORK_ORDER_DB_PASSWORD
\getenv ai_app_password AI_DB_PASSWORD
\getenv analytics_reader_password ANALYTICS_DB_PASSWORD

SELECT format('CREATE ROLE flyway_owner LOGIN PASSWORD %L', :'flyway_owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'flyway_owner')
\gexec

SELECT format('CREATE ROLE work_order_app LOGIN PASSWORD %L', :'work_order_app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'work_order_app')
\gexec

SELECT format('CREATE ROLE ai_app LOGIN PASSWORD %L', :'ai_app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_app')
\gexec

SELECT format('CREATE ROLE analytics_reader LOGIN PASSWORD %L', :'analytics_reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader')
\gexec

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;

GRANT USAGE, CREATE ON SCHEMA public TO flyway_owner;
GRANT USAGE ON SCHEMA public TO work_order_app, ai_app, analytics_reader;

ALTER DEFAULT PRIVILEGES FOR ROLE flyway_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO work_order_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO work_order_app;
