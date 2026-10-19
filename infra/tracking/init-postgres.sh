#!/bin/sh
(
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$TRACKING_PG_PASSWORD" <<'SQL'
CREATE ROLE mlflow LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER DATABASE mlflow_lab OWNER TO mlflow;
SQL
)
