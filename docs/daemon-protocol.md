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
| `options` | object | Command options using CLI option names without leading `--`. Defaults to `{}`. Boolean flags use JSON booleans. |
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
fragments. Surrounding context and recovery advice can vary:

| Output fragment | State |
| --- | --- |
| `Session '<name>' is not open` | The requested session is absent. |
| `Tab '<name>' is not open` | The requested named tab is absent from the session. |
| `has been closed` | Playwright reports a closed page, context, or browser. The surrounding text comes from Playwright and can vary with its version. |
| `requires an auth token` | The request omitted `token`. |
| `rejected the auth token` | The supplied token is invalid or stale. Read the token file fresh before retrying. |

## Compatibility

`protocol` is independent of the package version. Any incompatible change to
framing, request fields (`command`, `args`, `options`, `cwd`, `token`), reply
fields (`success`, `output`), authentication, or the documented state-detection
fragments requires a protocol increment. Optional additive fields and new
commands do not require an increment. Clients can reject unsupported protocol
versions before sending requests.
