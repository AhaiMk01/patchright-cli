# Daemon client protocol

The TCP interface is a public interface for third-party clients. The current
protocol version is **1**, defined by `PROTOCOL_VERSION` in `daemon.py`.

## Discovery

`patchright-cli daemon-info --json` prints one JSON object and exits without
starting a daemon or browser, connecting to the daemon, or reading the token.
For example, on a Unix installation with home directory `/home/alice`:

```json
{"version": "0.9.0", "protocol": 1, "port": 9321, "token_path": "/home/alice/.patchright-cli/daemon-9321.token"}
```

`version` is the installed package version. `protocol` describes that installed
version's wire contract, not a handshake with a running daemon. After upgrading,
restart any older daemon before using the new contract.

`--port=N` and `--port N`, before or after the command, select the port and its
token path. The default port is 9321. There is currently no port or token-path
environment override. `token_path` is absolute and follows the current user's
home directory. The file need not exist for discovery to succeed. Discovery
reports configuration, not daemon readiness, and never prints the token value.
Older installations without this command do not advertise a protocol version.

## Transport and authentication

Connect to `127.0.0.1` on the discovered port. Each connection carries one
request and one reply, after which the daemon closes the connection. Both
messages use the same framing:

1. Four bytes containing the unsigned payload byte length in big-endian order
   (`struct.pack("!I", length)`).
2. That many bytes of UTF-8 encoded JSON, with an object as the top-level value.

Reads must loop until the entire header and payload arrive. There is no newline
or other delimiter. An incomplete request can close without a reply.

The daemon reads a request before checking its token, so it bounds that read:

- The whole request must arrive within 30 seconds of connecting, or the daemon
  closes the connection without a reply. Commands themselves and replies have
  no time limit.
- A request whose header announces more than 64 MiB (67,108,864 bytes) gets a
  `success: false` reply whose `output` starts with `Daemon error: request of`,
  sent without reading the body, and the connection closes.

The daemon writes a new secret to `~/.patchright-cli/daemon-<port>.token` when
it starts and removes its token on clean shutdown. Read the discovered
`token_path` as UTF-8 text and strip surrounding whitespace before each request.
The token may change when the daemon restarts. Send it in the request's `token`
field. Keep the value private. The client must run as a user who can read this
file. A missing file can indicate that the daemon has not started or has stopped.

## Request fields

| Field | JSON type | Meaning |
| --- | --- | --- |
| `command` | string | Command name, such as `list`, `open`, or `snapshot`. |
| `args` | array of strings | Positional command arguments. Defaults to `[]` if omitted. |
| `options` | object | Command options using CLI option names without leading `--`. Defaults to `{}`. Values are the JSON the CLI sends: a string for `--name=value`, `true` for a bare flag, and an object for a few structured options (for example `viewport` is `{"width": 1280, "height": 720}`). |
| `cwd` | string or null | Absolute caller working directory for relative paths and output files. Omitted or null uses the daemon's working directory. |
| `token` | string | Current authentication token read from `token_path`. Required. |

`options.session` and `options.tab` select a session and named tab; both default
to `default`. Command-specific arguments and options follow CLI help.

## Reply fields

Every reply carries an explicit boolean `success` and a string `output`, on
both success and failure. `success: false` indicates authentication, command,
or daemon errors. `output` contains the result or a human-readable error.
Additional fields, such as `snapshot_path`, are command-specific. Clients must
ignore unknown fields. No structured error codes are currently provided.

Clients can detect these states in failed replies by matching these output
fragments. Surrounding context and recovery advice can vary. The fragments in
this table are part of the protocol:

| Output fragment | State |
| --- | --- |
| `Session '<name>' is not open` | The requested session is absent. |
| `Tab '<name>' is not open` | The requested named tab is absent from the session. |
| `requires an auth token` | The request omitted `token`. |
| `rejected the auth token` | The supplied token is invalid or stale. Read the token file fresh before retrying. |
| `Daemon error: request of` | The request exceeded the 64 MiB limit. |

### Idempotent no-ops

A mutation whose target state already holds succeeds instead of failing:
`success` is `true` and `output` ends with ` (no-op)`. This covers `close` and
`detach` of a session that is not open (the output still contains
`Session '<name>' is not open`), `close` of a named tab that is not open,
`video-stop`, `tracing-stop` and `codegen-stop` with nothing running,
`video-start` and `tracing-start` while already running, `video-hide-actions`
when callouts are off, `unroute` of a pattern that is not routed (or with no
routes at all), `cookie-delete`, `localstorage-delete` and
`sessionstorage-delete` of an absent name or key, `highlight --hide` with
nothing highlighted, and `profile-delete` of a profile that does not exist.
`go-back` and `go-forward` at the edge of the history still fail.

### Structured data

Some replies carry an optional `data` field next to `output`, holding the same
result as JSON. `output` stays the human-readable text. When a list reply shows
fewer items than exist, `total` holds the full count.

| Command | `data` |
| --- | --- |
| `url` | `{"url"}` |
| `title` | `{"title"}` |
| `list` | `[{"name", "status", "url"}]`, where `status` is `open` or `attached` |
| `tab-list` | `[{"index", "title", "url", "active"}]` |
| `cookie-list` | `[{"name", "domain", "expires"}]` |
| `localstorage-list`, `sessionstorage-list` | `[{"key", "value"}]` |
| `route-list` | `[{"pattern", "status"}]` |
| `console` | `[{"level", "text"}]`, plus `total` when older messages are cut off |
| `network` (alias `requests`) | `[{"id", "method", "status", "url"}]`, plus `total` when older requests are cut off |
| `profile-list` | `[{"name", "size", "session"}]`, where `size` is bytes and `session` is the holding session or `null` |
| `find` | `[{"ref", "role", "name"}]`, plus `total` when `--limit` cut the hits |

An empty list is `[]`, and its `output` states the zero explicitly, for example
`0 cookies in this session`. Commands not listed here send no `data`. More
fields or commands can gain `data` later, so read only the keys you need.

Best effort, not covered by the compatibility rules below: a command on a page,
context or browser that has closed usually fails with Playwright's own message
containing `has been closed`. That text comes from Playwright and can change
with its version without a protocol increment.

## Compatibility

`protocol` is independent of the package version. Any incompatible change to
framing, request limits, request fields (`command`, `args`, `options`, `cwd`,
`token`), reply fields (`success`, `output`), authentication, or the
state-detection fragments in the table above requires a protocol increment.
Raising a request limit is not incompatible. Optional additive fields and new
commands do not require an increment. Clients can reject unsupported protocol
versions before sending requests.
