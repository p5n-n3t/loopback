# Permissions

Loopback enforces two independent layers for every operation: a **policy
layer** (what paths/commands/tools are allowed, and whether a sensitive
action needs a bounded approval) and, for shell execution, an **OS sandbox
layer** (the kernel or OS actually restricting what a spawned process can
touch). Policy decisions happen in `runtime/policy.py`; sandbox enforcement
happens in `runtime/sandbox.py`. Neither depends on the calling MCP client
behaving correctly.

## Policy profiles

Set with `loopback policy profile <name>` or from the dashboard's Security
page. `loopback policy show` prints the current effective policy.

| Profile | Alias | Filesystem | Shell / process | Fleet | Approvals | Sandbox |
|---|---|---|---|---|---|---|
| `locked` | | none | disabled | disabled | everything blocked | not required |
| `read-only` | | read-only everywhere permitted | shell allowed, process control disabled | enabled | everything needs approval | **required** |
| `restricted` | `standard` | read/write in permitted locations (home by default) | enabled | enabled | sensitive command patterns need approval | **required** |
| `development` | | same as restricted plus more permissive everyday tooling | enabled | enabled | fewer approval prompts | **required** |
| `admin` | `trusted` | read/write in permitted locations | enabled | enabled | no soft approvals; hard denies still apply | used when available, not required |
| `custom` | | every field below honored exactly as saved | — | — | — | as configured |

`restricted` is the default for a new install. `admin`/`trusted` is for a
machine you deliberately administer through AI — treat switching to it the
same way you'd treat handing out `sudo` access.

## Filesystem policy

`allowed_roots` is a list of `{"path", "mode": "rw"|"ro"}`; `denied_roots` is
a list of paths that are never reachable regardless of `allowed_roots`.
Defaults deny:

```
~/.ssh  ~/.gnupg  ~/.aws  ~/.azure  ~/.kube  ~/.docker/config.json
~/.config/1Password  ~/.config/gh/hosts.yml  ~/.config/loopback  ~/.netrc
```

(the last one is Loopback's own state directory and credential store — it
denies itself by default). See
[examples/policy.example.json](../examples/policy.example.json). Every path
check happens server-side in `check_path`; a model being told "the file is
out of scope" is not the boundary, the policy check is.

## Per-tool and per-command policy

- `allowed_tools`/`denied_tools` gate which MCP tools are reachable at all —
  `["*"]` plus explicit denies, or an exact allowlist for a narrow connector
  (e.g. file inspection only, no `execute`).
- `require_approval_patterns` — regexes that, when matched against a shell
  command, require a bounded dashboard approval (`approval_ttl_seconds`,
  default 900s, with a limited use count) before the command runs. The
  default set flags `sudo`, recursive `rm`/`unlink`, service
  stop/disable/mask, destructive package-manager operations, `docker/podman
  rm`/`system prune`/`volume rm`, `shutdown`/`reboot`/`poweroff`/`halt`, and
  forced `git push`.
- `deny_command_patterns` — hard denies with **no** approval path, in every
  profile including `admin`: recursive deletion of a filesystem root,
  `mkfs`/`wipefs`, `dd ... of=/dev/...`, a classic fork-bomb pattern, and
  `chmod 777 /`.

A blocked call returns `approval_required:<id>: approve it in the Loopback
dashboard (Security → Approvals) or use a less restrictive profile`, or for
a hard deny, a plain refusal with no approval id at all.

## Per-target policy (fleet)

Each registered Loopback node and SSH target gets its own entry under
`policy.json`'s `targets` key. A remote target **never inherits the local
machine's `allowed_roots`**; absent any override it starts from `{"path":
"~", "mode": "rw"}` on that target's own home directory. SSH targets default
to `"enforcement": "gateway-path-check"` — the gateway evaluates
`check_command`/`check_path` before sending anything over SSH, since there
is no OS sandbox on a machine reached only by SSH. See
[FLEET.md](FLEET.md) for the full per-target example.

## OS sandbox backends

Selected automatically (`sandbox.backend: "auto"`) unless pinned:

| Backend | Platform | Notes |
|---|---|---|
| `landlock` | Linux 5.13+ | ctypes syscalls, no helper binary; ABI-gated feature set (ABI 2 adds rename/link-across-directories support, ABI 3 adds truncate, ABI 5 adds device ioctl). **Cannot subtract a denied subtree from an allowed tree** — when a denied root sits inside an allowed root, the allowed root's children are granted individually instead, so a file created directly in that top-level directory becomes accessible at the next command. |
| `bwrap` | Linux (fallback) | bubblewrap user namespaces; masks denied subtrees with tmpfs, so it *can* express "deny this subtree inside an allowed tree" where Landlock cannot. Runs the shell in a new mount namespace — tools that inspect host mounts see the sandboxed view. |
| `sandbox-exec` | macOS | Seatbelt profiles; Apple marks the binary deprecated but it still ships and enforces. Supports explicit deny rules. |
| `none` | Windows / unsupported kernels | No OS enforcement for shell commands — they run with the full rights of the service account. File tools (read/write/list/patch/search) are still enforced server-side regardless. |

`loopback doctor` (and `POST /api/admin/sandbox/self-test`) proves
enforcement by actually running a denied-read and an allowed-list command
through the active backend rather than just reporting a backend name.

Profiles with `sandbox_required: true` (`read-only`, `restricted`,
`development`) refuse to run shell commands when no backend is enforcing,
unless the install explicitly sets `sandbox.acknowledge_unenforced: true` —
a deliberate, visible risk acceptance rather than a silent default. `admin`
uses the sandbox when available but does not require it.

Every sandboxed child process also has credential-shaped environment
variables stripped before exec (`LOOPBACK_TOKEN`, `AWS_SECRET_ACCESS_KEY`,
`GITHUB_TOKEN`, `OPENAI_API_KEY`, etc.) so a shell command cannot read the
gateway's own secrets out of its environment.

## Effective permissions

`GET /api/admin/permissions?target=local` (or `policy_status` over MCP)
returns the fully resolved view for one target: profile, roots,
shell/process/fleet flags, and the live sandbox status — this is what to
check before assuming a capability is available, rather than inferring it
from the profile name alone.

## See also

- [AUTHENTICATION.md](AUTHENTICATION.md) — scopes are a separate, coarser
  layer above this policy (an OAuth client without `loopback:exec` never
  reaches `execute` regardless of policy).
- [FLEET.md](FLEET.md) — per-target policy and SSH enforcement.
- [TOOLS.md](TOOLS.md) — which tools are destructive/read-only and what
  scope each needs.
- [examples/policy.example.json](../examples/policy.example.json)
