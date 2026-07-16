# Mobile sync contract

`mobile-sync-v1.schema.json` defines the stable JSON fields shared by the FastAPI server and Flutter client.
Breaking changes require a new schema version; v1 clients reject any other `schema_version`.

All timestamps are UTC RFC 3339 strings. `cooked_on` is a local calendar date (`YYYY-MM-DD`).
Recipe bodies are server-owned; mobile write operations are limited to `practice_log` entities.
