"""Atomic local NDJSON event import. Imported verification is historical metadata."""

import base64
import json

from .store import EventStore, redact_headers, time_bound


def import_ndjson(path: str, source: str) -> int:
    """Validate all rows first; allocate fresh ids and never enqueue forwards."""
    rows = []
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("expected event object")
            for key in ("source", "received_at", "body", "verification", "reason"):
                if not isinstance(event.get(key), str):
                    raise ValueError(f"invalid {key}")
            time_bound(event["received_at"])
            if event["verification"] not in {"valid", "invalid", "unsigned", "no_secret"}:
                raise ValueError("invalid verification")
            headers = event["headers"]
            if not isinstance(headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            ):
                raise ValueError("invalid headers")
            encoded = event.get("body_base64")
            if encoded is not None:
                if not isinstance(encoded, str):
                    raise ValueError("invalid body_base64")
                base64.b64decode(encoded, validate=True)
            request_id = event.get("request_id")
            if request_id is not None and not isinstance(request_id, str):
                raise ValueError("invalid request_id")
            diagnosis = event.get("diagnosis")
            if diagnosis is not None and (
                not isinstance(diagnosis, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in diagnosis.items())
            ):
                raise ValueError("invalid diagnosis")
            rows.append((
                event["source"], event["received_at"], json.dumps(redact_headers(headers)),
                event["body"], event["verification"], event["reason"], encoded, request_id,
                json.dumps(diagnosis) if diagnosis is not None else None,
            ))
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"line {number}: {error}") from error
    store = EventStore(path)
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO events "
            "(source, received_at, headers, body, verification, reason, body_base64, "
            "request_id, diagnosis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)
