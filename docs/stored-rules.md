# Stored rules

SQLite now includes a rules table, created automatically for existing databases. `EventStore.save_rule(definition)` validates named definitions with the shared rule parser before an atomic upsert. `stored_rules()` reads them in name order.

At application startup, configured file rules are loaded first, followed by stored rules. A file rule wins a name collision; the stored definition remains intact. File contents are never rewritten. Invalid stored definitions fail startup rather than silently changing forwarding. An explicit `create_app(rules=...)` continues to override both sources.

Rules are loaded at startup in this step. Restart after programmatic changes. HTTP mutation endpoints and live refresh are separate features.
