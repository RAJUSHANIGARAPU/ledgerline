"""Database-level guarantees for the ledger and the audit trail.

- journal entries and lines cannot be updated or deleted (corrections are reversals)
- lines can only be added in the transaction that created their entry
- every entry balances and has at least two lines (checked at commit)
- entries cannot be dated inside a locked period
- audit events are append-only

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION ledgerline_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only (% refused)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END $$;

        CREATE FUNCTION journal_entry_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE locked date;
        BEGIN
            SELECT locked_until INTO locked FROM administrations WHERE id = NEW.administration_id;
            IF locked IS NOT NULL AND NEW.entry_date <= locked THEN
                RAISE EXCEPTION 'period is locked up to %', locked USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;

        CREATE FUNCTION journal_entry_after_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM set_config(
                'ledgerline.open_entries',
                coalesce(current_setting('ledgerline.open_entries', true), '') || ',' || NEW.id || ',',
                true);
            RETURN NULL;
        END $$;

        CREATE FUNCTION journal_line_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF position(',' || NEW.entry_id || ',' IN
                        coalesce(current_setting('ledgerline.open_entries', true), '')) = 0 THEN
                RAISE EXCEPTION 'journal entry % is already posted; lines cannot be added', NEW.entry_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END $$;

        CREATE FUNCTION journal_entry_check_balance() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE eid integer; n integer; d bigint; c bigint;
        BEGIN
            IF TG_TABLE_NAME = 'journal_entries' THEN
                eid := NEW.id;
            ELSE
                eid := NEW.entry_id;
            END IF;
            SELECT count(*), coalesce(sum(debit_cents), 0), coalesce(sum(credit_cents), 0)
              INTO n, d, c FROM journal_lines WHERE entry_id = eid;
            IF n < 2 OR d <> c THEN
                RAISE EXCEPTION 'journal entry % does not balance (lines %, debit %, credit %)', eid, n, d, c
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;

        CREATE TRIGGER journal_entries_period_lock BEFORE INSERT ON journal_entries
            FOR EACH ROW EXECUTE FUNCTION journal_entry_before_insert();
        CREATE TRIGGER journal_entries_mark_open AFTER INSERT ON journal_entries
            FOR EACH ROW EXECUTE FUNCTION journal_entry_after_insert();
        CREATE CONSTRAINT TRIGGER journal_entries_balanced AFTER INSERT ON journal_entries
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_check_balance();
        CREATE CONSTRAINT TRIGGER journal_lines_balanced AFTER INSERT ON journal_lines
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION journal_entry_check_balance();
        CREATE TRIGGER journal_lines_same_transaction BEFORE INSERT ON journal_lines
            FOR EACH ROW EXECUTE FUNCTION journal_line_before_insert();
        """
    )
    for table in ("journal_entries", "journal_lines", "audit_events"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION ledgerline_refuse_change();
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION ledgerline_refuse_change();
            """
        )


def downgrade() -> None:
    for table in ("journal_entries", "journal_lines", "audit_events"):
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
    op.execute(
        """
        DROP TRIGGER journal_lines_same_transaction ON journal_lines;
        DROP TRIGGER journal_lines_balanced ON journal_lines;
        DROP TRIGGER journal_entries_balanced ON journal_entries;
        DROP TRIGGER journal_entries_mark_open ON journal_entries;
        DROP TRIGGER journal_entries_period_lock ON journal_entries;
        DROP FUNCTION journal_entry_check_balance();
        DROP FUNCTION journal_line_before_insert();
        DROP FUNCTION journal_entry_after_insert();
        DROP FUNCTION journal_entry_before_insert();
        DROP FUNCTION ledgerline_refuse_change();
        """
    )
