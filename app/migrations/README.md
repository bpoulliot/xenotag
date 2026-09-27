# `state.db` migrations

Alembic revisions for xenotag's scan index. They live under `app/` because the image ships only
`app/`, and they are driven from code (`app/migrate.py`): there is no `alembic.ini`, so run the
wrappers below, not the `alembic` CLI.

## Adding a revision

1. Change the model in `app/state.py`.
2. Generate the revision from the difference, numbering it after the newest file in
   `versions/`:

       python -m app.migrate revision -m "add media_state.foo" --rev-id 0002

3. **Read the generated file.** Autogenerate misses renames (it writes a drop plus an add, which
   loses the column's data) and server defaults. SQLite changes are rendered as
   `batch_alter_table`, which rebuilds the table, so that is expected.
4. `black app/ && ruff check app/`, then `python -m app.migrate check`, which must print
   *models match the migration head*.
5. Add a test to `tests/test_migrations.py` that upgrades a database holding rows through
   the new revision and asserts what the rows look like afterwards.

Prefer **additive** revisions: a new nullable column or a new table. An older image can still
read the database afterwards. A revision that drops, renames or retypes a column can't be
rolled back without a backup. Say so in the release notes, and have the operator back up
`state.db` with its `-wal` and `-shm` files first.

## Rules

- Never edit a revision that has shipped in a release; add a new one.
- Never go back to `create_all()`, or to a hand-written `ALTER`, on the startup path.
- `0001` is the baseline: the exact schema every pre-migration release built. Its
  `downgrade()` refuses on purpose, because below it there is no schema to go back to.
