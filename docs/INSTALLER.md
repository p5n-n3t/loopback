# Installation and network choices

Run the existing installer as the account that will own Loopback. Existing CLI verbs remain available.
`loopback help` lists commands. `loopback report` shows a readable status table. Use `loopback report --json` for automation.
Install steps show a sequence number, result, and elapsed time. Terminal colors respect `NO_COLOR` and `TERM=dumb`.
Download and package-manager duration is unknown. Loopback does not invent a remaining-time estimate.

Before the exposure menu, the installer checks local binaries, Tailscale state and existing handlers, and SSH aliases.
These checks are read-only. They do not contact remote hosts, import keys, adopt routes, or change Tailscale configuration.
An unavailable check remains unavailable. It is not reported as no infrastructure.

Mesh access connects machines. Endpoint exposure lets an MCP client reach this gateway. These are different choices.

| Selection | Reachability | Essential information |
| --- | --- | --- |
| Local | This computer | None |
| Tailscale Serve | Tailnet devices | An authenticated Tailscale daemon |
| Tailscale Funnel | Public internet | Tailscale, an available supported HTTPS port, explicit public confirmation |
| Named Cloudflare | Public or Access-protected | Stable hostname and tunnel credentials/configuration |
| ngrok | Public internet | Official agent, fixed account hostname, existing v3 config, explicit public confirmation |

Existing external Serve/Funnel routes remain protected. Loopback does not reset a handler belonging to RotoXY or another application.
The dashboard can configure another supported HTTPS port when 443 is occupied.
See [TUNNEL-PROVIDERS.md](TUNNEL-PROVIDERS.md) for evaluation and unsupported choices.

Example new ngrok installation:

```sh
./install.sh --ingress ngrok --host YOUR-ASSIGNED-HOSTNAME --ngrok-config /path/to/ngrok.yml --yes
```

The installer does not download an unverified ngrok binary or request its account secret.
Install the official agent and configure its credentials using the provider's supported flow first.
After installation, `loopback up` starts the saved gateway and enabled owned connector processes.

## Credentials

A fresh installation generates a dashboard username/password and a separate reusable connector setup key.
They appear once in terminal output. Save them in your password manager. Install reports contain no secret values.
Existing dashboard passwords remain unchanged when installation repeats.

Use `loopback auth account` to set a dashboard username and password interactively.
Use `loopback auth connector-key` to generate a missing setup key.
Use `loopback auth connector-key --rotate` to replace it.
Rotation prevents future consent with the old key. Existing OAuth grants remain valid until separately revoked.
The key is not a dashboard password or direct MCP bearer credential.
`loopback auth enroll` retains the one-time local recovery flow. `loopback token` retains machine-token recovery.
See [AUTHENTICATION.md](AUTHENTICATION.md) for the full credential boundaries.

## Optional installation settings

Pass `--settings-file /path/to/settings.json` or set `LOOPBACK_SETTINGS_FILE` for this installer invocation.
The file is read on the installation host. It must be a JSON object.
Supported top-level keys are `port`, `bind`, `public_host`, `display_name`, `node_kind`, `ingress`, `discovery`, and `maintenance`.
Explicit `--port` and `--host` arguments take precedence over corresponding settings.
The regular configuration validator applies. Unsupported keys fail installation rather than weaken policy or authentication.

```json
{
  "display_name": "Workstation",
  "discovery": {"excluded_targets": ["ora2", "ora4"]},
  "maintenance": {"probe_interval_seconds": 60}
}
```

| Key | Meaning and use |
| --- | --- |
| port | Local listener port, 1–65535. Use a free port. |
| bind | Local listener address. Keep 127.0.0.1 unless direct network access is intentional. |
| public_host | Stable externally reachable hostname, without scheme/path. This does not create a tunnel. |
| display_name | Human-readable machine label in the dashboard and topology. |
| node_kind | local-computer, remote-physical, remote-vm, container, gateway, or unknown. |
| ingress | Provider sections with enabled flags, stable hostnames, and credential file paths. See TUNNEL-PROVIDERS.md and ADMIN-API.md. |
| discovery.excluded_targets | SSH aliases excluded from inventory. Excluded hosts are not contacted. |
| maintenance.probe_interval_seconds | Cached probe interval. Zero disables periodic probes. Positive values must be at least 10. |
| maintenance | Other validated update, history, audit, backup, and log-retention settings from config defaults. |

A first-time local inventory cannot identify every service on an unknown network.
Register authorized SSH aliases with `loopback ssh add`, then install workers through Services as needed.
Use existing SSH configuration or supply host/user/port through the documented SSH target workflow.
Do not put private keys, bearer tokens, or proxy passwords in this settings file.
See [WORKERS.md](WORKERS.md) and `loopback ssh --help` for optional host metadata.
More complete authorized target information makes the inventory more complete. Failed hosts remain partial or unavailable.
