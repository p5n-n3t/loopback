# Security

Loopback exposes machine capabilities to authenticated MCP clients. Treat any
reachable Loopback endpoint — even a tailnet-only one — as administrative
infrastructure, and read [docs/PERMISSIONS.md](docs/PERMISSIONS.md) and
[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) before exposing one beyond
your own machine.

## Network boundary

- The MCP origin binds to `127.0.0.1` by default (`bind` in `config.json`).
- Public or tailnet access comes through an explicit ingress choice:
  Tailscale Serve (tailnet-only) or Funnel (public internet, requires
  confirming the public-exposure prompt), Cloudflare Tunnel (named or
  token tunnel), or your own reverse proxy. See
  [docs/TAILSCALE.md](docs/TAILSCALE.md), [docs/CLOUDFLARE.md](docs/CLOUDFLARE.md).
- Do not bind directly to all interfaces unless you understand and intend
  the resulting exposure.
- `allowed_origins` constrains which web origins can complete a
  browser-based OAuth flow against this server.

## Authentication model

Loopback is both an MCP resource server and an OAuth 2.1 authorization
server. Several credential types exist and are deliberately not
interchangeable:

1. **OAuth access/refresh tokens** — issued to clients that complete
   authorization code + mandatory PKCE (S256). Discovery via
   `/.well-known/oauth-protected-resource` and
   `/.well-known/oauth-authorization-server`. Clients may self-register via
   `/oauth/register` (Dynamic Client Registration) or present a Client ID
   Metadata Document URL as their `client_id`; redirect URIs must be HTTPS or
   a loopback HTTP address (RFC 8252 §7.3). Refresh tokens rotate on use;
   reusing a revoked refresh token revokes its whole token family.
2. **Dashboard-minted API tokens** (`lbk_<id>_<secret>`) — for MCP clients
   that only support a static bearer header. Created, scoped, and revoked
   from the dashboard or `loopback auth token create|list|revoke`. These are
   comparable to a GitHub personal access token: narrowly scoped and
   independently revocable, never the shared machine secret.
3. **The machine token** — a long-lived local bootstrap/recovery credential
   used to set the initial dashboard password and as an emergency
   authentication path. It is not meant to be the primary credential handed
   to a third-party AI product; prefer an API token or OAuth for that.
   `auth.legacy_machine_token_mcp` additionally gates whether the machine
   token is accepted directly on the MCP endpoint (off by default on a fresh
   v4 install; migrated v3 installs keep it on until you turn it off).

Scopes are `loopback:read`, `loopback:write`, `loopback:exec`,
`loopback:fleet`, and `loopback:admin`. **`loopback:admin` is never granted
to an OAuth client or API token** — admin-only actions require a dashboard
session. The legacy bare `loopback` scope string expands to
read+write+exec+fleet (never admin).

Rotate credentials after suspected disclosure:

```bash
loopback auth rotate-machine-token   # new machine token; does not affect OAuth
loopback auth rotate-signing-key     # invalidates every outstanding OAuth access token immediately
loopback auth token revoke <id>      # revoke one API token
loopback auth revoke-client <id>     # revoke an OAuth client and its grants
```

There is no top-level `loopback rotate-token` command — rotation is always
one of the `loopback auth ...` subcommands above, because machine-token,
signing-key, and per-token rotation are independent operations with
different blast radii.

## Filesystem policy

Every filesystem path is evaluated server-side, never trusted from the
client. Default denied roots cover common credential locations:
`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`, `~/.kube`,
`~/.docker/config.json`, `~/.config/1Password`, `~/.config/gh/hosts.yml`,
`~/.config/loopback` (Loopback's own state), and `~/.netrc`. Customize
`allowed_roots`/`denied_roots` per machine, and independently per registered
fleet target — a remote target never inherits the local machine's roots.
A model instruction is not a security boundary; the server-side path check
is. See [docs/PERMISSIONS.md](docs/PERMISSIONS.md).

## OS sandbox enforcement

Where the platform supports it, shell execution additionally runs inside an
OS-level sandbox: Landlock (Linux, ABI-gated by kernel version), bubblewrap
as a Linux fallback, or `sandbox-exec`/Seatbelt on macOS. Windows has no OS
sandbox backend — file tools are still enforced server-side there, but shell
commands run with the full rights of the service account. Policy profiles
that require sandbox enforcement refuse to run shell commands when no
backend is active, rather than silently running unsandboxed; this can be
overridden per-install with `acknowledge_unenforced`, which is a deliberate
risk acceptance, not a default. See [docs/PERMISSIONS.md](docs/PERMISSIONS.md)
for backend caveats, including the Landlock limitation that a denied
subtree cannot be subtracted from a broader allowed tree.

## Per-tool and per-command policy

`allowed_tools`/`denied_tools` are enforced before a call reaches the MCP
tool implementation, so one endpoint can be published with a narrow
capability surface (e.g. read-only file inspection, no execution).

Command patterns fall into three tiers: ordinary (runs immediately),
soft-approval (needs a bounded, expiring dashboard approval — the set and
strictness varies by profile), and hard-deny (refused unconditionally, no
approval path — e.g. recursive deletion of a filesystem root, disk-wipe
utilities, a fork bomb pattern). Hard-deny patterns apply in every profile,
including `admin`.

Do not confuse Loopback's own policy with a client's confirmation UI — an
MCP client may still ask its user to approve a tool call, and MCP tool
annotations (read-only/destructive/idempotent) are untrusted hints to that
client, not an authorization mechanism. Loopback's server-side policy is the
actual enforcement boundary and is never bypassed by a client-side approval.

## Policy profiles

`locked` (no file/shell/process/fleet access), `read-only` (read-only
filesystem, no process control, sandbox required), `restricted` (default;
alias `standard` — read/write in permitted locations, soft approvals,
sandbox required), `development` (restricted plus fewer approval prompts for
everyday tooling), `admin` (alias `trusted` — no soft approval prompts, hard
denies still apply, sandbox used when available but not required), and
`custom` (every field honored exactly as saved). Moving a machine to
`admin`/`trusted` is a deliberate decision for a machine you intend to
administer through AI, not a default. Full definitions:
[docs/PERMISSIONS.md](docs/PERMISSIONS.md).

## PTYs, jobs, and process control

- Persistent-terminal input goes through the same command policy as one-shot
  execution when it carries textual commands.
- Process control refuses to target PID 1 or the Loopback server process
  itself.
- Background jobs run in their own process group so they can be stopped as a
  unit, with bounded, rotated log output under the config directory.

A sophisticated shell command can have side effects a regex-based policy
cannot fully anticipate. Use filesystem boundaries, OS account permissions,
the sandbox backend, and least-privilege policy profiles as layered
defenses, not as a single control.

## Browser and native desktop automation

Both are disabled by default (`allow_browser`/`allow_desktop`). Browser
automation requires a separately installed `agent-browser` executable and
can operate authenticated web sessions — treat it with the same caution as
shell execution. Native desktop automation (Linux/X11 only) lets an
authenticated caller interact with the focused GUI application via
coordinates, text, and key events; keep it disabled on headless or
infrastructure-only machines.

## Document operations

DOCX/XLSX/PDF helpers obey the same filesystem policy as ordinary file
tools. Prefer writing to a new output file over in-place edits when
formatting fidelity matters.

## Dashboard

The dashboard uses its own session model — an HttpOnly, SameSite=Strict
`loopback_admin` cookie plus an `X-Loopback-CSRF` header on every
state-changing request — independent of OAuth/API-token auth used by MCP
clients. Sessions default to a 12-hour TTL. A dashboard login can be
established with the account password or, as a recovery path, the machine
token. All dashboard sessions can be revoked at once from Security →
Sessions, or `POST /api/admin/auth/sessions/revoke-all`.

## Audit data

Local audit history lives in `<config dir>/loopback.db` (OAuth/auth state is
kept separately in `<config dir>/auth.db`). The audit store records tool
name, status, duration, target, and a bounded detail string — never
Authorization headers, bearer/API tokens, or MCP request/response bodies.
Retention is bounded (`maintenance.audit_retention_days`, default 90).
Protect the config directory with filesystem permissions accordingly: every
state file Loopback writes there is created mode `0600`.

## Fleet credentials

Each registered Loopback node and SSH target has its own credential/identity
file, stored separately from the public registry (`nodes.secrets.json` next
to `nodes.json`; SSH targets reference an `identity_file` path and never
store key material directly). Never reuse one bearer credential or SSH key
across an entire fleet. Per-target policy is independent and never widens
local access.

## Reporting

Do not open public issues containing credentials, tokens, private keys,
recovery codes, or exploit details. Report security issues privately to the
maintainers listed in the repository.
