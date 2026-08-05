import logging

from django.apps import AppConfig
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)


class CasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cases"
    verbose_name = "Cases & Workflow"

    # Guards the one-off, idempotent schema sync so it runs at most once
    # per process.
    _schema_synced = False

    def ready(self):
        # This project historically runs on `migrate --run-syncdb` (it ships no
        # migrations). syncdb creates brand-new tables but never ALTERs existing
        # ones, so columns added to a model later are missing on an established
        # database. To keep upgrades friction-free we make sure the few columns
        # introduced after the initial schema exist, adding them on demand.
        #
        # IMPORTANT: we must NOT touch the database here. Querying during app
        # initialization is discouraged by Django and raises a RuntimeWarning
        # ("Accessing the database during app initialization is discouraged"),
        # because the app registry may not be fully ready yet. Instead we register
        # a handler on the `connection_created` signal and run the sync lazily on
        # the FIRST database connection — which happens after startup (e.g. on
        # runserver's migration check or the first request) — guarded to run only
        # once. Connecting a signal does not hit the database, so it is safe to do
        # at import/ready time.
        import os
        if os.environ.get("FT_SKIP_SCHEMA_SYNC") == "1":
            return
        connection_created.connect(self._schema_sync_once,
                                   dispatch_uid="cases_schema_sync")

    def _schema_sync_once(self, sender, connection, **kwargs):
        # Runs the first time any DB connection is opened, well after the app
        # registry is ready (so no RuntimeWarning). Idempotent and safe on both
        # SQLite and PostgreSQL.
        if self._schema_synced:
            return
        self._schema_synced = True
        # Stop firing on every subsequent connection.
        connection_created.disconnect(dispatch_uid="cases_schema_sync")
        try:
            self._ensure_added_columns(connection)
        except Exception:
            # Still never fatal — a missing column surfaces its own clear error
            # if it truly cannot be added, and refusing to start would be worse.
            # But it is no longer *silent*: this step issuing hand-written DDL
            # and failing without a trace is exactly the "silent drift" problem
            # (item 7 of the review), where the development machine, the live
            # server and every fresh install quietly diverge and nobody finds
            # out until something breaks in production. Logging it makes the
            # divergence visible the moment it happens.
            logger.exception(
                "cases schema sync failed. The database may be missing columns "
                "or tables the models expect. Check `showmigrations cases` and "
                "apply any unapplied migrations."
            )

    def _ensure_added_columns(self, connection):
        # Map: table -> [(column, DDL type with default), ...]
        wanted = {
            "cases_case": [
                ("price_upgraded_two_stage", "boolean NOT NULL DEFAULT 0"),
                ("upgraded_two_stage", "boolean NOT NULL DEFAULT 0"),
            ],
            "cases_lineitem": [
                ("client_row", "integer NOT NULL DEFAULT 0"),
            ],
            "cases_caseform": [
                ("two_stage", "boolean NOT NULL DEFAULT 0"),
            ],
            "cases_caseevent": [
                ("two_stage", "boolean NOT NULL DEFAULT 0"),
            ],
            # Added by migration cases/0006. Listed here too because this net is
            # still in service until it is retired, and a column it does not
            # cover is a column that can go missing silently. That matters more
            # than usual for this one: it is read while freezing a document's
            # signatory during a handoff, inside the workflow's transaction, so
            # a missing column does not fail politely — it fails the handoff.
            "cases_signaturesnapshot": [
                ("signer_title", "varchar(160) NOT NULL DEFAULT ''"),
            ],
        }

        with connection.cursor() as cursor:
            existing_tables = set(connection.introspection.table_names(cursor))
            for table, columns in wanted.items():
                if table not in existing_tables:
                    continue  # syncdb will create it fresh with all columns
                have = {c.name for c in connection.introspection.get_table_description(cursor, table)}
                for col, ddl in columns:
                    if col in have:
                        continue
                    # Postgres uses TRUE/FALSE; rewrite the boolean default if needed.
                    ddl_sql = ddl
                    if connection.vendor == "postgresql":
                        ddl_sql = ddl.replace("boolean NOT NULL DEFAULT 0",
                                              "boolean NOT NULL DEFAULT FALSE")
                    try:
                        cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {ddl_sql}')
                        logger.warning(
                            "cases schema sync added column %s.%s outside the "
                            "migration history. This should not happen on a "
                            "database that is up to date with migrations.",
                            table, col,
                        )
                    except Exception:
                        # Column may have been added concurrently, or the type
                        # string isn't supported. Not fatal, but no longer
                        # invisible — see the note in _schema_sync_once.
                        logger.exception(
                            "cases schema sync could not add column %s.%s", table, col)

            # Tables introduced after the initial schema (the ``cases`` app runs on
            # syncdb, which never creates a table for a model that lacks its own
            # migration). Create them here, idempotently, so exports/audit work on
            # both fresh and established databases without a migration dance.
            self._ensure_added_tables(connection, cursor, existing_tables)

    def _ensure_added_tables(self, connection, cursor, existing_tables):
        # The "cases_case" guard is what keeps this a genuine last resort rather
        # than a competitor to the migration that now owns this table.
        #
        # This step runs on the first database connection — which is the one
        # `manage.py migrate` itself opens, before a single migration has been
        # applied. On a fresh database that meant the hand-written, foreign-key
        # -less table was created first, and migration 0008 then always found it
        # already there and did nothing. The table the migration was written to
        # produce would never have existed on any install.
        #
        # An empty database has no cases_case either, so requiring it to be
        # present distinguishes "established database, migration not applied yet
        # — patch it" from "brand new database, let migrate do its job".
        if ("cases_caseexportlog" not in existing_tables
                and "cases_case" in existing_tables):
            if connection.vendor == "postgresql":
                cursor.execute(
                    'CREATE TABLE IF NOT EXISTS "cases_caseexportlog" ('
                    '"id" bigserial NOT NULL PRIMARY KEY, '
                    '"case_id" bigint NOT NULL, '
                    '"actor_id" integer NULL, '
                    '"form_kind" varchar(10) NOT NULL DEFAULT \'\', '
                    '"form_version" integer NULL, '
                    '"side" varchar(10) NOT NULL DEFAULT \'\', '
                    '"fmt" varchar(20) NOT NULL DEFAULT \'\', '
                    '"label" varchar(120) NOT NULL DEFAULT \'\', '
                    '"created_at" timestamp with time zone NOT NULL)'
                )
            else:
                cursor.execute(
                    'CREATE TABLE IF NOT EXISTS "cases_caseexportlog" ('
                    '"id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, '
                    '"case_id" integer NOT NULL, '
                    '"actor_id" integer NULL, '
                    '"form_kind" varchar(10) NOT NULL DEFAULT \'\', '
                    '"form_version" integer NULL, '
                    '"side" varchar(10) NOT NULL DEFAULT \'\', '
                    '"fmt" varchar(20) NOT NULL DEFAULT \'\', '
                    '"label" varchar(120) NOT NULL DEFAULT \'\', '
                    '"created_at" datetime NOT NULL)'
                )
            try:
                cursor.execute(
                    'CREATE INDEX IF NOT EXISTS "cases_caseexportlog_case_created" '
                    'ON "cases_caseexportlog" ("case_id", "created_at")')
            except Exception:
                logger.exception(
                    "cases schema sync could not index cases_caseexportlog")
            # Table creation was previously the one thing this step did with no
            # trace at all — only column additions were logged. That made the
            # start-up log look clean precisely while the patcher was doing the
            # work that still mattered, which is how you end up disabling it and
            # discovering on a fresh install that nothing else creates the
            # table. Migration cases/0008 now creates it; reaching this line
            # means the migration has not been applied.
            logger.warning(
                "cases schema sync created table cases_caseexportlog outside "
                "the migration history. Apply migration cases/0008 — after that "
                "this branch should never run again.")

        # The cases_casecurrencylog branch that used to live here has been
        # removed. That table is now created by migration
        # cases/0007_casecurrencylog_into_migrations, which is the only place
        # that should ever create it. Leaving the raw-DDL version in place
        # would have made the migration useless: this patcher runs on the very
        # first database connection, which is also what the migration loader
        # opens, so the hand-written table won the race on every fresh install
        # and the migration always found it "already there". The hand-written
        # version had no foreign keys and a different index from the model.
