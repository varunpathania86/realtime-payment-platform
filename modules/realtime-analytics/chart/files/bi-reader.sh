#!/bin/sh
# Creates or rotates the SELECT-only dashboard user. SQL goes through stdin so the
# reader password is not visible in process arguments.
set -eu
case "$BI_READER_USER" in ""|*[!a-z0-9_]*) echo "BI reader user must be lowercase snake_case" >&2; exit 1;; esac
case "$BI_READER_PASSWORD" in ""|*[!A-Za-z0-9]*) echo "BI reader password must be alphanumeric" >&2; exit 1;; esac
case "$BI_READER_DATABASE" in ""|*[!a-z0-9_]*) echo "BI reader database must be lowercase snake_case" >&2; exit 1;; esac
clickhouse-client --host "$CLICKHOUSE_HOST" --user "$CLICKHOUSE_USER" \
  --password "$CLICKHOUSE_PASSWORD" --multiquery <<SQL
CREATE USER IF NOT EXISTS ${BI_READER_USER} IDENTIFIED WITH sha256_password BY '${BI_READER_PASSWORD}';
ALTER USER ${BI_READER_USER} IDENTIFIED WITH sha256_password BY '${BI_READER_PASSWORD}' SETTINGS readonly = 2;
REVOKE ALL ON *.* FROM ${BI_READER_USER};
GRANT SELECT ON ${BI_READER_DATABASE}.* TO ${BI_READER_USER};
SQL
