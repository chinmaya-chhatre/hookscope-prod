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

## HTTP replacement and deletion

`PUT /api/rules/{name}` replaces an existing stored rule and returns 200 with
its normalized definition. Send a complete rule object: omitted optional fields
reset to parser defaults. The name may be omitted from the body; if supplied,
it must match the path. Renaming is not supported. Invalid definitions return
422 without changing storage or active forwarding. A missing stored name returns
404; PUT never creates a new rule.

`DELETE /api/rules/{name}` returns 200 with a typed JSON confirmation,
`{"name": "the-rule-name", "deleted": true}`, after removing the stored definition. Deleting a missing name returns 404. Captured events and
forward history remain available. New events stop matching the deleted rule;
already selected or in-flight forwards can finish.

Both endpoints use the configured administrative authentication. File-managed
names return 409, including when a stored definition is shadowed by a file rule.
An explicit `create_app(rules=...)` override also returns 409. File contents and
shadowed stored definitions are never changed through these endpoints.

Writes are serialized within the serving process and committed before replacing
its active rule list. Failed writes leave that list unchanged. Other processes
retain their startup configuration until restarted. There is no cross-process
update broadcast or optimistic version check: concurrent successful replacements
use the last committed definition in storage.

## Pause and resume

Set `"enabled": false` on a rule to pause it without removing its definition.
The field must be a JSON boolean; strings, numbers and null are rejected.
An omitted flag defaults to true, preserving existing file and stored rules.
GET responses and serialized definitions include `enabled` when false; true is
omitted for compatibility.

For stored rules, PUT the complete definition to `/api/rules/{name}` with
`enabled` set to false or true. PUT still replaces the whole rule: omitting
the flag resets it to true. Pausing takes effect for subsequent matching in
the serving process and survives restart. File rules use the same flag but
must be changed in their file and reloaded by restarting.

Paused rules do not match new events or enter new delivery batches. Rule-based
transform previews report `matches: false` while still displaying the transform
output. Manual rule resends return 409 with code `rule_disabled`; dead letters
show `resendable: false` until the rule is enabled again. Captured events,
forward history and stored definitions remain intact.

Already selected forwards and their retries can finish using their original
rule snapshot. Pausing is not cancellation. Explicit ad-hoc replay to a target
URL is independent of rule state. Other server processes still need restart
to load stored-rule changes.
