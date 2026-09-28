"""Create ba_fact immutability triggers with explicit project deletion support.

Revision ID: 0002_ba_fact_immutability
Revises: 0001_athena_source_evidence
Create Date: 2026-09-25
"""

from alembic import op

revision = "0002_ba_fact_immutability"
down_revision = "0001_athena_source_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Trigger function blocking UPDATE unconditionally, and blocking DELETE
    # unless explicit project deletion context is active in the current transaction.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ba_fact_block_mutation() RETURNS trigger AS $$
        DECLARE
            allow_del text;
            del_project_id text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'ba_fact is append-only: UPDATE is not permitted (id=%)', OLD.id;
            END IF;

            IF TG_OP = 'DELETE' THEN
                BEGIN
                    allow_del := current_setting('athena.allow_project_deletion', true);
                    del_project_id := current_setting('athena.deleting_project_id', true);
                EXCEPTION WHEN OTHERS THEN
                    allow_del := NULL;
                    del_project_id := NULL;
                END;

                IF allow_del = 'true' AND del_project_id = OLD.project_id::text THEN
                    RETURN OLD;
                END IF;

                RAISE EXCEPTION 'ba_fact is append-only: DELETE is not permitted (id=%)', OLD.id;
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # 2. Row-level trigger on ba_fact
    op.execute(
        """
        DROP TRIGGER IF EXISTS ba_fact_no_update_delete ON ba_fact;
        CREATE TRIGGER ba_fact_no_update_delete
        BEFORE UPDATE OR DELETE ON ba_fact
        FOR EACH ROW EXECUTE FUNCTION ba_fact_block_mutation();
        """
    )

    # 3. Trigger function blocking TRUNCATE unconditionally
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ba_fact_block_truncate() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ba_fact is append-only: TRUNCATE is not permitted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # 4. Statement-level trigger on ba_fact
    op.execute(
        """
        DROP TRIGGER IF EXISTS ba_fact_no_truncate ON ba_fact;
        CREATE TRIGGER ba_fact_no_truncate
        BEFORE TRUNCATE ON ba_fact
        FOR EACH STATEMENT EXECUTE FUNCTION ba_fact_block_truncate();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ba_fact_no_truncate ON ba_fact;")
    op.execute("DROP TRIGGER IF EXISTS ba_fact_no_update_delete ON ba_fact;")
    op.execute("DROP FUNCTION IF EXISTS ba_fact_block_truncate();")
    op.execute("DROP FUNCTION IF EXISTS ba_fact_block_mutation();")
