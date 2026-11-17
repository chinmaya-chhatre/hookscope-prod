# Purging stored events

Run `python -m hookscope purge --keep-last 100` to retain the newest 100 events.
Use `--older-than ISO_TIMESTAMP` to remove older events, or omit both limits to
apply the server retention policy. This permanently deletes matching events.
Set `--url` for a remote server and `HOOKSCOPE_ADMIN_PASSWORD` for authentication.
`--json` prints removed and remaining counts. Exit status 0 means success; 2 means
invalid arguments, an unreachable server, authentication failure or an API error.
