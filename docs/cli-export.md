# Export events

Run `python -m hookscope export > events.ndjson` to export stored events newest
first. Each line is a complete API event object, including body and headers.
Use `--url`, `--source` and `--verification` to select the server and filters.
Set `HOOKSCOPE_ADMIN_PASSWORD` for a protected server. Protect the output as
sensitive webhook data. Pagination excludes new arrivals after the first page;
concurrent deletion or retention can still remove events during export.
A server or pagination error exits 2 and reports an incomplete export on stderr.
Discard partial output on a nonzero exit. This is not a transactional backup.
