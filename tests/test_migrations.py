"""The Alembic migrations make exactly the tables that models.py describes."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from paperplane import create_app, db
from paperplane.db import Base


def test_upgrade_on_an_empty_database_matches_the_models(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "fresh.sqlite"), "DATABASE_URL": None})
    with app.app_context():
        db.upgrade_db()
        db.upgrade_db()  # running it again changes nothing
        with db.engine().connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
            tables = set(inspect(connection).get_table_names())
    # Nothing left for "alembic revision --autogenerate" to write: the
    # migrations and the models agree. If this fails after changing
    # models.py, make a new migration (see AGENTS.md).
    assert differences == []
    assert set(Base.metadata.tables) | {"alembic_version"} == tables


def test_upgrade_db_command(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "cli.sqlite"), "DATABASE_URL": None})
    result = app.test_cli_runner().invoke(args=["upgrade-db"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


def test_bulk_deleted_parents_have_an_index_for_each_child(tmp_path):
    """Deleting a demo team cascades through its rows. Without an index that STARTS with
    the foreign key, each cascade reads the whole child table (Tally Ho!'s lesson)."""
    missing = []
    for table in Base.metadata.sorted_tables:
        leading = {index.columns.values()[0].name for index in table.indexes}
        leading |= {constraint.columns.values()[0].name for constraint in table.constraints
                    if constraint.__class__.__name__ in ("UniqueConstraint", "PrimaryKeyConstraint") and constraint.columns}
        for key in table.foreign_keys:
            if key.ondelete == "CASCADE" and key.parent.name not in leading:
                missing.append(f"{table.name}.{key.parent.name}")
    assert missing == []
