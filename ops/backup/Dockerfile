# Nightly dump, off-site copy with restic (BSD-2-Clause) and the dead-man's-switch ping.
FROM restic/restic:0.19.1 AS restic

FROM postgres:16-alpine
RUN apk add --no-cache curl ca-certificates
COPY --from=restic /usr/bin/restic /usr/bin/restic
COPY manifest.sh table-counts.sql backup.sh entrypoint.sh /opt/ledgerline/
RUN chmod 0755 /opt/ledgerline/backup.sh /opt/ledgerline/entrypoint.sh
ENTRYPOINT ["/opt/ledgerline/entrypoint.sh"]
