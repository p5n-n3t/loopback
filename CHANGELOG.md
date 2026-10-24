# Changelog

## 4.0.0 — 2026-10-22

Loopback becomes a full self-hostable MCP control plane: a proper OAuth 2.1
authorization server, a much larger policy and fleet model, and an
operations dashboard with a first-run setup wizard.

### Added

- RFC 8707 resource indicators, RFC 9728 protected-resource metadata, and an
  RFC 8414-shaped authorization-server metadata document.
- Dynamic Client Registration (`/oauth/register`) and Client ID Metadata
  Document support for OAuth clients.
- Refresh-token rotation with family revocation on detected reuse.
- Dashboard-minted, independently scoped and revocable API tokens
  (`lbk_<id>_<secret>`) for bearer-only MCP clients, replacing the shared
  machine token as the recommended per-client credential.
- `loopback:read` / `loopback:write` / `loopback:exec` / `loopback:fleet` /
  `loopback:admin` scopes, with `loopback:admin` never grantable to an OAuth
  client or API token.
- Six policy profiles (`locked`, `read-only`, `restricted`/`standard`,
  `development`, `admin`/`trusted`, `custom`) with per-target overrides for
  registered fleet nodes and SSH targets — remote targets never inherit the
  local machine's allowed roots.
- OS sandbox enforcement for shell execution: Landlock on Linux, a
  bubblewrap fallback, and `sandbox-exec`/Seatbelt on macOS, with a
  self-test and explicit, non-default opt-out for unenforced execution.
- Managed SSH targets with BatchMode probing, topology classification
  (tailscale/lan/public), and server-side command/path policy
  (`gateway-path-check`) for every remote call.
- A fleet/topology view: nodes, SSH targets, ingress, and MCP clients as one
  graph with real probe-derived edge statuses.
- A rewritten operations dashboard (vanilla JS, strict CSP) covering
  Overview, Topology, Local machine, Fleet, Filesystem, Shell, MCP tools,
  Authentication, Cloudflare, Tailscale, SSH config, Jobs, Logs &
  diagnostics, Security, Backup & recovery, Updates, and Settings, plus a
  first-run Welcome wizard.
- Cloudflare named/token tunnel ingress with a selectable access policy
  (OAuth, Cloudflare Access, or both) alongside Tailscale Serve/Funnel.
- `loopback doctor [--deep]`, `loopback report`, `loopback repair`,
  `loopback backup`/`restore`, `loopback rollback`, and `loopback upgrade`
  with automatic backup/stage/swap/health-check/rollback.
- `npx loopback-mcp install` as a third install path alongside `install.sh`
  and `install.ps1`, verifying a release's SHA-256 before extracting it.
- Bundled Agent Skills (`skills/loopback-core`, `loopback-fleet`,
  `loopback-admin`, `loopback-troubleshooting`, `loopback-mcp-development`)
  packaged into the Agent Plugin archive by `scripts/build_plugin.py`.

### Changed

- Policy profile names: the v3 `standard`/`trusted` profiles are now aliases
  for `restricted`/`admin`; existing installs are mapped automatically on
  upgrade.
- Configuration moved from `config.env` to a structured, validated
  `config.json`, migrated automatically on first v4 startup
  (`migrate_legacy_env`).
- Identity and config-directory resolution now go through one shared
  `runtime/identity.py` module (sudo-aware account detection, XDG/Windows
  `%LOCALAPPDATA%` config-dir resolution) used consistently by the CLI,
  installers, and runtime.
- The fleet/gateway documentation moved to `docs/FLEET.md`;
  `docs/GATEWAY.md` is now a pointer to it.

### Security

- Refresh-token reuse now revokes the entire token family and records a
  `refresh_token_reuse_detected` audit event instead of only the one token.
- The machine token is repositioned as a bootstrap/recovery credential, not
  the recommended per-client MCP credential.
- Rotating the OAuth signing key invalidates every outstanding OAuth access
  token immediately; rotating the machine token does not affect OAuth
  issuance.
- Every state file under the config directory is written atomically
  (temp file + `os.replace`) and created mode `0600`.
- Dashboard sessions use an HttpOnly, SameSite=Strict cookie plus a
  double-submit CSRF header on every mutating request, and can all be
  revoked at once from Security → Sessions.

## 3.0.0 — 2026-10-06

Loopback grows from a portable remote MCP bridge into a self-hostable AI machine
control plane.

### Added

- bounded one-shot shell execution
- background jobs with incremental output
- persistent PTY terminal sessions
- structured process listing and signaling
- server-side filesystem allow/deny roots
- per-tool allow/deny policy
- standard, trusted, read-only, and locked profiles
- bounded approvals for sensitive command patterns
- hard-deny command patterns
- MCP read-only/destructive/idempotent annotations
- local SQLite MCP audit history
- authenticated operations dashboard
- direct multi-node gateway and node routing tools
- ergonomic node read/execute/diagnostics wrappers
- DOCX text read/replace tools
- XLSX range read/write tools
- PDF text/merge/page-extraction tools
- optional structured browser automation adapter
- optional Linux/X11 native desktop adapter with MCP screenshots
- portable Agent Plugin package builder
- Python 3.11/3.13 CI and runtime/integration tests

### Changed

- OAuth now uses authorization code + mandatory PKCE S256.
- OAuth clients receive short-lived signed access and refresh tokens instead of
  the permanent machine bearer token.
- Static bearer authentication remains supported for compatible MCP clients.
- Installer deploys the modular runtime rather than a single server file.
- CLI now exposes dashboard, policy, and node management commands.

### Security

- common credential directories are denied by the default filesystem policy
- remote-node credentials are separated from node metadata
- dashboard uses an HttpOnly, SameSite=Strict session cookie
- audit storage excludes bearer credentials and MCP request bodies
- browser and native-desktop automation are disabled by default
- destructive capabilities remain subject to both client approval policy and
  Loopback's own machine-side policy
