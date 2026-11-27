# Stored rules

## Dashboard list

Open `/rules` or choose **Rules** in the dashboard navigation to inspect the
effective rules in the serving process. Expand a rule to see its source and
event-type filters, payload format, retry policy and normalized JSON definition,
including predicates and transforms. Enabled and paused rules are both listed.
Pausing stops new matches; already selected deliveries and retries can finish.

Each rule is labelled **Stored**, **File (read-only)** or **Application
(read-only)**. File rules win name collisions with stored definitions, so only
the effective file definition is shown. An explicit application override shows
only its supplied rules. The list follows the same order and definitions as
`GET /api/rules`.

Opening or expanding a rule does not change it, send a webhook or create history.
Use **Add stored rule** or a stored rule's **Edit rule** link to edit its complete
JSON definition. The initial example includes all supported settings. As you type,
the server validates the draft with the same parser as the write API, after a
short delay. Invalid JSON, bad targets, providers, predicates, transforms, retry
settings and conflicting names keep Save disabled. Validation sends no webhook,
stores no draft and creates no history. Drafts stay in the page, not browser storage.

Save uses POST for creation or PUT for full replacement and rechecks all rules
and conflicts. Editing cannot rename a rule. Omitted fields reset to defaults.
File and application rules have no edit controls, and the server rejects attempts
to edit them. Application overrides also hide creation. JavaScript is required;
the administrative API remains available without it.

Successful saves reload the list. A failed save retains the draft and requires
another edit/validation before retrying. If a network failure leaves the save
unconfirmed, check the rules list before retrying. Concurrent successful edits
use the last committed definition. Other server processes retain their startup
configuration until restarted, as before.

`POST /api/rule-validation` accepts one named rule and returns its normalized
definition. Supply `?edit=existing-name` when replacing an existing stored rule.
It uses administrative authentication and returns the same error shape as writes,
including 422 for invalid definitions, 409 for conflicts/read-only rules and 404
for missing edits. Validation is advisory; it does not reserve a name or version.

The page uses the same configured authentication as the dashboard and API.
Definitions may include sensitive target URLs or transform constants; protect
administrative access before exposing the instance. The empty state explains
how to supply rules when none are effective.

## Storage and loading

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

## Rule change history

`GET /api/rule-history` returns committed stored-rule changes, newest first.
Each record contains `id`, `name`, `changed_at` (UTC ISO 8601), `actor`,
`before` and `after`. Creation has a null `before`; deletion has a null
`after`. Replacement, including pause/resume, includes both definitions.
Snapshots preserve the stored JSON; older sparse definitions may omit optional
fields. An unchanged definition does not add a history record.

Use `name` for an exact rule-name filter. Deleted names remain queryable, and
recreating a name continues its history. The endpoint returns a list, with
`limit` defaulting to 50 and capped at 500. When another page exists,
`X-Next-Cursor` holds the last history id: pass it as `before_id` with the
same filter for older records. These ids are independent of event ids.
A name with no recorded changes returns an empty list.

For example, in a local unprotected development instance:

```sh
curl 'http://localhost:8000/api/rule-history?name=notify-slack&limit=20'
```

Use the configured administrative authentication on protected instances.
History has the same access requirements as rule definitions and can contain
sensitive target URLs or transform constants. It never records Authorization
headers, passwords or supplied Basic usernames as actor metadata.

| Actor | Meaning |
| --- | --- |
| admin_basic | Request authenticated with the shared administrator password using HTTP Basic |
| admin_bearer | Request authenticated with the shared administrator password using Bearer |
| anonymous | Request made while administrative authentication was disabled |
| programmatic | Direct EventStore write using its default actor context |

The current password is shared and accepts any Basic username. These labels
identify the access context, not a verified individual. A client-provided
username or actor header cannot provide trustworthy attribution, so neither is
used. Direct storage callers may select one of these contexts through the
keyword-only `actor` argument; it is application-supplied metadata.

The definition and its history record commit in the same SQLite write
transaction. Concurrent writers take the write lock before reading the
previous definition, so each before/after pair follows the actual commit
order. If writing history fails, the rule write rolls back and the serving
process keeps its previous active rules. Rejected, conflicting and missing-rule
requests produce no history.

Existing databases gain the history table automatically. Previous changes
cannot be reconstructed and are not fabricated. File edits, startup loading
and explicit embedded rules do not produce stored-rule history. File rules
remain read-only through the API.

History survives process restarts, rule deletion/recreation and event purging.
Event-retention settings do not expire it. Include it in database backups and
access controls, and plan disk capacity accordingly. There is no history
deletion API or dashboard in this interface. It is not a tamper-proof audit log:
a principal with database write access can alter the database.

## Transfer rules as JSON

Use **Export effective rules** on the Rules page, or `GET /api/rules/export`,
to download `hookscope-rules.json`. The document is `{"rules": [...]}` with the
same normalized definitions as the rules API. It includes enabled and paused
rules, filters, transforms, targets, and retry policies. File and application
rules export too, without their management origin. History and captured events
are excluded. Downloads can contain sensitive target URLs and transform values;
store and share them accordingly. Both transfer endpoints require the configured
administrative authentication.

Paste the document into **Import new rules** on the Rules page, or send it as
JSON to `POST /api/rules/import`. A successful response is HTTP 201 with the
normalized imported `rules` list. This is a create-only operation: every name
must be explicit, nonempty, unique in the document, and absent from existing
effective and stored rules. A conflict returns 409. Invalid JSON or definitions
return 422. No rule is overwritten, deleted, or imported partially. Definitions
and their creation history commit in one transaction; import sends no webhooks.
The imported rules become stored rules, including definitions exported from a
file on another installation.

Import accepts 1 to 100 rules and a body no larger than the smaller of 1 MiB and
the configured request body limit (413 when exceeded). The top-level object must
contain only `rules`; duplicate JSON object keys and non-finite numbers are
rejected. Empty exports are valid backups but have nothing to import. To copy a
large export, divide it into batches; each batch is independently atomic.
Imports are unavailable when rules were explicitly supplied by the embedding
application. Existing file rules remain read-only. Choose a new destination or
rename intended copies before importing an export that conflicts with existing names.

Imported rules activate for subsequent events in the receiving process. Other
processes must restart to load them, just as for individual rule creation. In-flight
forwards retain their selected definitions. If a response is lost, inspect the rule
list and history before retrying: the transaction may already have committed.
The dashboard retains the pasted draft on errors and never retries automatically.

## Custom forwarding headers

A rule can set outbound request headers in raw, transformed, or Slack mode:

```json
{
  "name": "billing",
  "target_url": "https://billing.example.com/webhooks",
  "headers": {
    "Authorization": "Bearer ${env:HOOKSCOPE_FORWARD_BILLING_TOKEN}",
    "X-Integration": "billing"
  }
}
```

Set `HOOKSCOPE_FORWARD_BILLING_TOKEN` in the HookScope process environment.
Use only environment variables dedicated to forwarding, with the
`HOOKSCOPE_FORWARD_` prefix. Values resolve on each attempt, including manual
resend. Missing, empty, or invalid values cause a failed attempt before any
request reaches the target. Header values must be ASCII and cannot contain
line breaks or leading or trailing whitespace.

Custom headers replace existing headers case-insensitively. Connection and
framing headers, proxy credentials, request IDs and HookScope's own headers
are reserved. Forwards with custom headers do not follow redirects, and omit
the target response body and transport error details from the result because
they may echo credentials. Configure the final HTTPS destination directly.

Admin API responses, exports, history, and the editor retain the configuration
templates. Use environment references for credentials because literal values
are visible in those definitions. Request previews redact custom header values
without looking up environment variables. Only administrators who are trusted
to configure the forwarding destinations should have rule editing access.

## Set a forwarding timeout

Set `"timeout_seconds": 2.5` in a file-backed or stored rule to override
the HTTP client timeout for every forward attempt. Use a JSON number between
0.001 and 120 seconds, inclusive. Omit the field or set it to `null` to use
the client default, which is 10 seconds in the standard server.

The limit applies separately to HTTP connect, read, write, and connection
pool waits. It is not a total delivery deadline. Timeouts count as transient
failures and use the rule's existing retry policy. The override applies to
automatic forwarding and redriving that rule. Manual replays use the client
default.
