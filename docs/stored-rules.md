# Stored rules

SQLite now includes a rules table, created automatically for existing databases. `EventStore.save_rule(definition)` validates named definitions with the shared rule parser before an atomic upsert. `stored_rules()` reads them in name order.

At application startup, configured file rules are loaded first, followed by stored rules. A file rule wins a name collision; the stored definition remains intact. File contents are never rewritten. Invalid stored definitions fail startup rather than silently changing forwarding. An explicit `create_app(rules=...)` continues to override both sources.

Rules are loaded at startup in this step. Restart after programmatic changes. HTTP creation is available as described below.

## HTTP creation

POST a single named rule object to `/api/rules` with the same fields as a file
rule. The shared parser validates its target, provider, predicates, transform
and retry policy. Success returns 201 and the normalized rule. GET returns the
effective rules, including predicates. The new rule is persisted before it
becomes active for subsequent events in the serving process.

Invalid definitions return 422 without persistence. A name already present in
file or stored rules returns 409 and is never overwritten. An application that
passes explicit rules to `create_app` rejects HTTP creation with 409. The API
uses the same configured authentication as other administrative endpoints.

Existing in-flight forwards retain their selected rules. Other running server
processes must restart to load newly stored definitions; this API does not
broadcast updates between processes. File rules remain read-only.

Rules without predicates omit `where` in both HTTP responses and `RuleOut` model dumps. Nonempty predicates are preserved when reading, creating and serializing rules.
