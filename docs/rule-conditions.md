# Rule conditions

Rules may include `where`, a list of conditions combined with AND. Example: `[{"path":"$.amount","op":"gt","value":100},{"path":"$.currency","op":"in","value":["usd","eur"]}]`.

Operators are `equals`, `in`, `gt`, `lt` and `exists`. Paths support `$`, named keys, quoted keys and array indexes, including negative indexes. Wildcards, filters and recursive descent are rejected. Missing values fail comparisons; `exists` accepts explicit null. Boolean and numeric values are distinct. Ordering requires finite numbers. Non-JSON bodies fail conditions. Omitted or empty conditions retain existing matching behavior. Source, verification and event-type checks still apply.
