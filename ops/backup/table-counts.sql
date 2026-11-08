-- One line per table: name, tab, row count. Run as the owner role (bypasses row-level security).
SELECT table_name || E'\t' || (xpath('/row/c/text()', query_to_xml(
    format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'
ORDER BY table_name;
