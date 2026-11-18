# Import captured events

Export with `python -m hookscope export > events.ndjson`, then import locally with `python -m hookscope import events.ndjson --db restored.db`. Use `-` for stdin. Stop the destination server before importing and back up its database.

All input is validated before inserts. Records receive fresh local ids; timestamps, body bytes and diagnosis metadata are preserved. Credentials in headers are redacted. Imports append, so repeating an import creates another copy. Forward attempts are not imported and no requests are sent. Verification values describe the original capture, not a fresh signature check. Treat input as trusted archival data.
