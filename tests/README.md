# tests/

Unit and invariant tests. `make check` = lint + tests + quick measure.

Tests and `make smoke` never touch the project database or the paid Model API (D-016): they run on
`<name>_test` (or `TEST_DATABASE_URL`), created on first use, and pytest exits before any test if the
database name does not end in `_test`. `MODEL_API_KEY` is blanked for both.
