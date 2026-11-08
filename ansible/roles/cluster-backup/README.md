# Cluster backups

Each discovered CNPG cluster is backed up with one compressed SQL dump per
connectable database, including `template1`, and a separate `pg_dumpall
--globals-only` dump of roles and tablespaces. PostgreSQL's non-connectable
`template0` is excluded. The manifest maps database names to archive files;
older manifests remain supported by `scripts/restore-homelab.sh`.

Database dumps are individually consistent, but are taken at different times.
The globals dump includes password hashes: protect the backup share and any
copies as secrets. The backup process validates gzip streams, reports CNPG
failures, and retains old backups if CNPG discovery or backup fails. Schedule
regular isolated restore tests; a successful dump alone does not prove a full
recovery will work.

When the role mounts the SMB share, it requests owner-only file and directory
permissions. An already-mounted share keeps its existing options; the role
warns if those options expose it to other local users. Synology ACLs also need
to restrict access independently of the client-side mount permissions.

The restore script restores selected dumps into **existing** databases. It
does not create databases or restore globals automatically. For a new cluster,
reconcile roles, passwords, ownership, tablespaces, extensions, and database
creation with CNPG and Crossplane before restoring database dumps. Globals are
provided for manual disaster recovery; blindly applying them to a running
cluster can conflict with existing or operator-managed roles.

Backups are on the same Synology system as the database storage. Keep a separate
offsite or independent copy, and consider PostgreSQL physical backups and WAL
archiving for point-in-time recovery between the daily logical dumps.
