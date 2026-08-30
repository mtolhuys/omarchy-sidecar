# Protocol v1

The helper serves HTTP only on loopback. Tailscale Serve supplies the private HTTPS transport. All API responses use JSON except bounded theme images and server-sent events.

## Authentication

Authenticated requests use `Authorization: Bearer <device credential>`. Credentials are random, independently revocable, delivered once after local approval, and stored server-side only as salted verifiers. Cross-origin requests and API query strings are rejected.

## Pairing

- `POST /api/v1/pair/request`: exact `{secret, device:{name,platform,clientVersion,protocol,clientInstanceId?}}`.
- `POST /api/v1/pair/status`: exact `{requestId,pendingCapability}`.

The QR secret lives in the URL fragment, lasts 120 seconds, is consumed by the first valid request, and derives three matching words from the pairing transcript. Approval requires an unlocked desktop and exact locally selected scopes.

Current clients send `clientInstanceId` as `sci1_` plus 256 bits of base64url entropy persisted separately from the credential. The helper stores only its SHA-256 digest. On approval, a new credential for the same digest atomically replaces older credentials and terminates their streams. The editable name and coarse platform are never used for deduplication. The optional form exists only so cached pre-v1009 clients fail safely without corrupting their pairing request.

## State

- `GET /health`: protocol range only; unauthenticated.
- `GET /api/v1/snapshot`: authenticated bounded snapshot.
- `GET /api/v1/events`: authenticated SSE with bounded streams, queues, and heartbeat.
- `GET /api/v1/theme-previews/<themeId>`: authenticated bounded image for a current installed theme.

Snapshots may contain:

- protocol, sequence, build IDs, coarse server time;
- device ID and exact permissions;
- locked/paused/available state;
- semantic theme colors and installed-theme names/opaque IDs;
- numbered workspaces and bounded application identity with opaque window IDs;
- fixed media transport availability/actions.

They never contain window titles, paths, URLs, commands, terminal data, media metadata, screen content, agent content, credentials, pairing material, or local filesystem names.

## Actions

`POST /api/v1/actions` accepts exact:

```json
{
  "requestId": "opaque bounded ID",
  "action": "fixed.enum",
  "parameters": {},
  "expectedSeq": 42
}
```

`expectedSeq` is optional. Allowed actions and scopes:

| Action | Exact parameters | Scope |
|---|---|---|
| `workspace.focus` | `{workspaceId}` | `control:workspace` |
| `window.focus` | `{windowId}` | `control:window-focus` |
| `window.moveToWorkspace` | `{windowId,workspaceId}` | `control:window-move` |
| `media.playPause` | `{}` | `control:media` |
| `media.previous` | `{}` | `control:media` |
| `media.next` | `{}` | `control:media` |
| `theme.set` | `{themeId}` | `control:theme` |
| `theme.backgroundNext` | `{}` | `control:theme` |
| `desktop.lock` | `{}` | `control:lock` |

Unknown fields, actions, IDs, parameter shapes, non-finite numbers, and stale targets fail closed. Request IDs are payload-bound for five minutes; exact replay returns the prior result and altered replay is rejected. Policy, target refresh, dispatch, and replay recording are serialized.

## Capability upgrade and unpair

- `POST /api/v1/capabilities/request`: exact `{requestId,scopes}`; only missing `control:window-move`, `control:theme`, `control:lock`, and `write:inbox` are remotely requestable.
- `POST /api/v1/capabilities/status`: exact `{requestId}`.
- `POST /api/v1/session/unpair`: exact `{requestId}`.
- `POST /api/v1/session/identify`: exact `{clientInstanceId}`; authenticated migration/binding for an existing credential.

Capability approval is local and unlocked. It preserves the existing device identity and credential. Existing devices never gain scopes silently.

`session/identify` is non-authorizing: it grants no scope and accepts no device selector. It can bind only the authenticated credential. If a newer credential is already bound to the same browser installation, the older authenticated session is removed and receives a terminal denial. A credential already bound to a different installation cannot be rebound.

Retired scope strings `read:carry`, `notify:carry`, `respond:carry-choice`, `control:focus-mode`, `control:agent-focus`, and `control:presentation` may be loaded from old state but are not requestable and map to no action.

## Limits

Drop adds: 5 files/batch; 25 MiB/file; 50 MiB/batch; 120 filename characters; 4 pending intents/device; 16 globally; 2 concurrent upload bodies; 20 files and 100 MiB/device/hour; 256 MiB free-space reserve; 64 KiB stream chunks; 5-minute intent lifetime.

Request headers, bodies, IDs, names, device count, streams, HTTP connections, workspaces, windows, themes, events, previews, rates, and timeouts are bounded in `sidecar/constants.py`. Safe errors expose a stable code and literal recovery without private state.

Finite HTTP responses use `Connection: close`. Duplicate framing-sensitive headers, transfer encodings, ranges, trailers, `Expect`, query strings on APIs, and request bodies on GET/HEAD/OPTIONS are rejected before dispatch. This deliberately removes keep-alive ambiguity after a rejected or partially read request.

`write:inbox` is the sole Drop scope. It is requested through the existing capability request/status flow and has the desktop label `Send allowed files to Sidecar Inbox`.

## Drop inbox

`POST /api/v1/inbox/intents` accepts exactly:

```json
{"requestId":"drop_opaque","files":[{"name":"Example.txt","mediaType":"text/plain","size":12,"sha256":"64 lowercase hex"}]}
```

The response contains a short-lived opaque `intentId` and one opaque `uploadId` per file. The request ID is payload-bound: an exact retry returns the same intent; altered replay is rejected. `POST /api/v1/inbox/uploads/<uploadId>` is a raw body with bearer authorization, exact `Content-Length`, and the declared `Content-Type`. Success returns a receipt containing only the opaque ID, safe final basename, media type, exact size/hash, and `saved` status. A byte-identical retry of the completed upload returns that receipt and creates no second file.

`POST /api/v1/inbox/cancel` accepts exactly `{"intentId":"…"}`. It invalidates unfinished capabilities and removes Sidecar staging; it never removes a committed file. Intent expiry is five minutes. Chunked transfer, trailers, ranges, unknown length, multipart upload bodies, and arbitrary destinations are outside the protocol.

Hourly Drop accounting is charged when an intent reserves its declared files and bytes. Cancellation, expiry, disconnect, or content-validation failure releases live capacity and staging but does not refund that hourly abuse-control allowance.
