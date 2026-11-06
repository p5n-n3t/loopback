<p align="center">
  <img src="assets/branding/loopback-symbol.png" width="64" height="63" alt="Loopback symbol">
  <br>
  <img src="assets/branding/loopback-wordmark.png" width="320" alt="Loopback gradient wordmark">
</p>

# Loopback

**Self-hosted Model Context Protocol (MCP) gateway for your computers and servers.**

Give an MCP client scoped access to files, shell commands, jobs, terminals, and SSH targets on machines you control. Loopback serves one authenticated Streamable HTTP endpoint and a dashboard. Start on localhost, then choose how to expose the gateway if a remote client needs it.

[![Linux CI](https://img.shields.io/github/actions/workflow/status/p5n-n3t/loopback/ci.yml?branch=main&style=flat-square&label=Linux%20CI)](https://github.com/p5n-n3t/loopback/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](docs/PLATFORMS.md)
[![MIT license](https://img.shields.io/badge/license-MIT-2da44e?style=flat-square)](LICENSE)

[Install](#install) · [Connect a client](#connect-a-client) · [Supported platforms](#supported-platforms) · [Documentation](docs/README.md) · [Download the release](https://github.com/p5n-n3t/loopback/releases/latest)

## What you can do

| Use case | Loopback gives you |
| --- | --- |
| Work on your machine from an MCP client | File and document tools, shell commands, background jobs, and persistent terminals on supported hosts. [Tool reference](docs/TOOLS.md) |
| Manage several machines through one gateway | Register Loopback nodes or plain SSH targets and set a separate policy for each. [Fleet and SSH](docs/FLEET.md) |
| Control what a client can access | OAuth 2.1 with PKCE or revocable API tokens, filesystem roots, tool permissions, command approvals, and OS sandboxing where available. [Authentication](docs/AUTHENTICATION.md) · [Permissions](docs/PERMISSIONS.md) |
| Connect from another network | Choose Tailscale Serve for private tailnet access, Tailscale Funnel or Cloudflare Tunnel for HTTPS ingress, or a reverse proxy you manage. [Deployment](docs/DEPLOYMENT.md) |
| Diagnose failed or interrupted calls | Inspect gateway receipts, policy decisions, dispatch, and exit status. Export redacted JSON or Markdown from Execution traces. [Execution traces](docs/EXECUTION-TRACES.md) |

## Install

Install on the machine the client should access. Use a regular user account and start with local ingress. Use the [GitHub release assets](https://github.com/p5n-n3t/loopback/releases/latest) for a versioned download, or follow the checkout commands below. See [installation details](docs/INSTALL.md) for prerequisites, noninteractive options, and distribution status.

### Linux and macOS

Requires Git, Bash, and Python 3.11+ with `venv`. The installer can offer prerequisite installation through supported package managers.

```bash
git clone https://github.com/p5n-n3t/loopback.git
cd loopback
./install.sh --non-interactive --ingress local
```

### Windows 10 and 11

Requires Git, PowerShell, and Python 3.11+. Omit `--non-interactive` to allow the installer to offer Python installation through `winget`.

```powershell
git clone https://github.com/p5n-n3t/loopback.git
Set-Location loopback
.\install.ps1 --non-interactive --ingress local
```

See [Windows limitations](docs/WINDOWS.md) for shell isolation, persistent terminals, and Task Scheduler.

### Verify the installation

Open a new terminal if the launcher directory was just added to your PATH.

```text
loopback status
loopback url
loopback dashboard --open
```

Use the URL printed by `loopback url` as the MCP server URL. The local default is `http://127.0.0.1:2026/mcp`. Run `loopback doctor --deep` if setup fails. For an existing installation, follow the [upgrade guide](docs/UPGRADE.md).

## Connect a client

1. Open the dashboard and review the tools, filesystem roots, and shell policy you want the client to use.
2. Add the server URL to an MCP client that supports Streamable HTTP. Use OAuth when supported. For a bearer-only client, create a revocable API token with the scopes it needs.
3. Start with a read-only tool such as `machine_info`, then check its receipt in **Execution traces**.

For a hosted client, configure HTTPS ingress reachable from that client. Loopback records calls that reach the authenticated gateway. A hosted client can reject an action before dispatch, in which case there may be no gateway receipt. See [web agent setup](docs/WEB-AGENTS.md) and [client capabilities](docs/CLIENT-CAPABILITIES.md).

### Ask in plain English

Once connected, tell the client what you want done in ordinary language.
Mention the machine, the folder or resource, and the limits of the task.

- “Using Loopback on the local machine, summarize the README in my project folder. Read only.”
- “On my registered build server, check available disk space and show the three fullest filesystems. Do not change anything.”
- “Check the status of the last Git commit in my project, then show the exit status and any Loopback receipt returned by the call.”

Loopback does not require a subscription. The chat or IDE still needs support
for custom MCP servers. **Claude Free** includes one remote custom connector.
**ChatGPT Free** has no documented custom MCP entitlement. ChatGPT Pro has
read/fetch access in developer mode, and Business and Enterprise/Edu have full
MCP support where enabled. See [client and account requirements](docs/CLIENT-CAPABILITIES.md).

### Use responsibly

An eligible web chat can use Loopback for coding tasks such as reading a
checkout, running tests, and applying authorized edits. The web client still
chooses the available models, thinking levels, tools, and approval flow.
Everyday chat allowances do not grant unlimited tool runs or Codex usage.
Use supported connection methods and respect provider limits and safety controls.
Only connect machines and accounts you are authorized to operate. You are
responsible for the access you grant and the actions you approve. Loopback is
provided as-is under the [MIT license](LICENSE). See
[ChatGPT Web vs. Codex CLI](docs/CLIENT-CAPABILITIES.md#chatgpt-web-vs-codex-cli).

## Supported platforms

| Gateway host | Service manager | Shell isolation and limits |
| --- | --- | --- |
| Linux, x86_64 and arm64 | systemd user service where available | Landlock or bubblewrap, depending on the host. |
| macOS 13+, Intel and Apple Silicon | launchd LaunchAgent | Seatbelt where `sandbox-exec` is available. |
| Windows 10/11, x86_64 | Task Scheduler at logon | No OS sandbox for arbitrary shell commands and no persistent PTY. |

GitHub CI runs the Python suite on Ubuntu with Python 3.11 and 3.13. See the [platform guide](docs/PLATFORMS.md) for capability differences and validation scope.

## Documentation

- **Set up** · [Installation](docs/INSTALL.md), [configuration](docs/CONFIGURATION.md), [deployment](docs/DEPLOYMENT.md), [upgrades](docs/UPGRADE.md)
- **Connect and secure** · [Authentication](docs/AUTHENTICATION.md), [permissions and sandboxing](docs/PERMISSIONS.md), [Cloudflare](docs/CLOUDFLARE.md), [Tailscale](docs/TAILSCALE.md)
- **Operate** · [Dashboard](docs/DASHBOARD.md), [fleet](docs/FLEET.md), [independent workers](docs/WORKERS.md), [execution traces](docs/EXECUTION-TRACES.md), [troubleshooting](docs/TROUBLESHOOTING.md)
- **Integrate** · [Tools](docs/TOOLS.md), [agent skills](docs/SKILLS.md), [plugin packaging](docs/PLUGIN.md), [architecture](docs/ARCHITECTURE.md), [admin API](docs/ADMIN-API.md)

See the [full docs index](docs/README.md), [changelog](CHANGELOG.md), and [security policy](SECURITY.md). Loopback is available under the [MIT license](LICENSE).
