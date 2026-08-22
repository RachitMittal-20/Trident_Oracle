# Webhooks

## `POST /v1/webhooks/invoices`

Lets an external system (a vendor's ERP, a procurement platform, anything
that isn't a human using the upload UI) submit an invoice directly, without
going through `POST /v1/invoices/upload`'s multipart form. Every request
must be signed; unsigned or incorrectly-signed requests are rejected before
the body is ever parsed as JSON.

### Signing a request

1. Take the raw request body bytes exactly as they will be sent — no
   re-serialization, no re-formatting. Signing happens on the exact bytes
   the server will read, since the server verifies against those same raw
   bytes before parsing anything.
2. Take the current Unix timestamp (seconds), as a string — e.g. `"1735689600"`.
3. Compute `HMAC-SHA256("{timestamp}.".encode() + raw_body, key=WEBHOOK_SIGNING_SECRET)`,
   hex-encoded.
4. Send three things with the request:
   - `X-Timestamp: <timestamp>` — the same string used in step 2
   - `X-Signature: <hex digest>` — the result of step 3
   - The JSON body itself, unmodified from what was signed

`WEBHOOK_SIGNING_SECRET` is a shared secret, provisioned out of band (see
`.env.example`) — the same value both sides configure, never transmitted on
the wire.

### Replay protection

`X-Timestamp` must be within 5 minutes of the server's clock, in *either*
direction — both a stale replayed request and a timestamp implausibly far in
the future are rejected. This is checked before the signature is computed
against, so an attacker who doesn't already have the secret can't extend the
window by guessing timestamps: forging a valid signature for a
still-in-window timestamp still requires the secret.

### Request body

Exactly one of `file_base64` or `file_url` must be present — supplying both,
or neither, is a `422`.

```jsonc
{
  "tenant_id": "3e2f6a10-...",           // required
  "filename": "invoice-2026-04.pdf",     // optional either way
  "file_base64": "JVBERi0xLjQK...",      // base64-encoded file bytes
  // -- OR --
  "file_url": "https://vendor.example.com/invoices/2026-04.pdf"
}
```

When `file_url` is given, the server fetches it server-side. That fetch is
restricted to `http`/`https` URLs whose hostname does not resolve to a
private, loopback, link-local, or otherwise reserved address (this closes
off the most common SSRF targets, including cloud metadata endpoints like
`169.254.169.254`) and never follows redirects.

### What happens on success

The same pipeline `POST /v1/invoices/upload` uses: magic-byte sniffing
(rejects anything that isn't actually a PDF/PNG/JPEG regardless of what the
request claims), SHA-256 content-hash dedupe scoped to the tenant, upload to
private storage, an `invoices` row created at `RECEIVED`
(`source_channel = 'webhook'`), an `extract` job enqueued, and an
`audit_log` entry (`action = 'invoice_received_via_webhook'`, `actor_type =
'system'`).

**Response: `202 Accepted`**

```json
{ "invoice_id": "b4b6...", "job_id": "9f2a..." }
```

### Error responses

| Status | Condition |
|---|---|
| `400` | `file_base64` is not valid base64, or `file_url` failed to fetch (unsafe scheme/address, unresolvable host, non-2xx response, or exceeds the size limit) |
| `401` | Missing `X-Timestamp`/`X-Signature`, a non-integer `X-Timestamp`, a timestamp outside the 5-minute window, or a signature that doesn't match |
| `409` | `content_hash` already exists for this tenant — the response body's `detail.invoice_id` is the existing invoice, exactly as `POST /v1/invoices/upload` returns it |
| `413` | Decoded/fetched file exceeds 10 MB |
| `415` | Bytes don't sniff as a supported format (PDF, PNG, or JPEG) |
| `422` | Request body isn't valid JSON against the schema above (including the "exactly one of `file_base64`/`file_url`" rule) |
| `500` | `WEBHOOK_SIGNING_SECRET` isn't configured on the server |

### Signature example — Python

```python
import hashlib
import hmac
import json
import time

import httpx

SECRET = "..."  # WEBHOOK_SIGNING_SECRET
BASE_URL = "https://api.trident-oracle.example"

body = json.dumps({
    "tenant_id": "3e2f6a10-0000-0000-0000-000000000000",
    "filename": "invoice.pdf",
    "file_base64": "...",
}).encode()  # exact bytes that will be sent

timestamp = str(int(time.time()))
signing_input = f"{timestamp}.".encode() + body
signature = hmac.new(SECRET.encode(), signing_input, hashlib.sha256).hexdigest()

response = httpx.post(
    f"{BASE_URL}/v1/webhooks/invoices",
    content=body,
    headers={
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    },
)
response.raise_for_status()
print(response.json())
```

### Signature example — curl

```bash
SECRET="..."                       # WEBHOOK_SIGNING_SECRET
BODY='{"tenant_id":"3e2f6a10-0000-0000-0000-000000000000","file_base64":"..."}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')

curl -X POST "https://api.trident-oracle.example/v1/webhooks/invoices" \
  -H "Content-Type: application/json" \
  -H "X-Timestamp: $TS" \
  -H "X-Signature: $SIG" \
  --data-raw "$BODY"
```

Note the `--data-raw "$BODY"` rather than curl's usual `-d`/`--data` (which
strips newlines) or `-d @file.json` (which may not byte-match what was
signed if the file has a trailing newline) — whatever bytes get signed must
be exactly the bytes curl sends.
