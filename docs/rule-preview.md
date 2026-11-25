# Preview rule matches

Expand an event on the dashboard or open its permalink to see the Rule match preview. Each current rule shows Matches or Does not match, with its configured `where` conditions. Evaluation uses the captured body, source, event type and verification result through the same matcher used for forwarding.

This is a read-only preview. It sends no requests and does not describe historical rules or guarantee target availability. Rules without transforms are included. Refresh the page after changing rule configuration. Names and condition data are escaped for display.

## Test rule on this event

On the dashboard or an event permalink, select a current rule and choose
**Test rule on this event**. The dry run reports its current match result and
shows the prepared POST URL, header pairs, exact body length, UTF-8 text when
decodable and base64 for the exact bytes. Raw, transformed and Slack rules use
the same request builder as forwarding, including request-id propagation,
forward markers, client defaults and transport-header handling. Binary bodies
remain byte-for-byte intact; base64 is authoritative for copying exact bytes.

Paused rules and captures rejected by verification do not match. For those,
or for filters that do not match, the displayed request is hypothetical and
labelled for inspection only. No target connection, retry, event, forward or
rule-history write occurs, even when a rule matches. Preparation does not
invoke client authentication flows, request hooks, redirects or transport
processing; those can alter a request during a real send. This is not a
connectivity test or a reproduction of a historical rule version.

The administrative API is `POST /api/events/{id}/rule-preview` with
`{"rule": "current-rule-name"}`. Its response contains `event_id`, `rule`,
`event_type`, `matches` and `request`. Header pairs preserve duplicate values.
Missing events/rules return 404; invalid requests return 422. The same configured
authentication as the dashboard protects it. A deleted rule is unavailable;
after editing a rule, the next dry run uses its current definition.

Credential header values are replaced with `[redacted]` and their names appear
in `redacted_headers`. Embedded URL usernames/passwords are removed. These
intentional redactions do not alter the actual forwarding request. Other headers,
target queries and bodies may contain sensitive data, so protect administrative
access and avoid sharing previews with unauthorized viewers. The browser displays
results as text and does not save them to browser storage.
