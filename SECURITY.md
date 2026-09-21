# Security

Loopback exposes machine capabilities to authenticated MCP clients. Treat any public Loopback endpoint as administrative infrastructure.

## Defaults

The packaged runtime uses the **standard** profile. Credentials and generated tokens live under `~/.config/loopback` and are excluded from Git.

The development machine may run a separately managed **trusted** profile with additional local capabilities. Do not copy development credentials into a repository, image, or another machine.

## Recommendations

- Keep the MCP origin bound to `127.0.0.1`.
- Put public ingress behind Cloudflare Tunnel or Tailscale Funnel.
- Rotate the Loopback token after any suspected disclosure.
- Never commit `~/.config/loopback/token`, Cloudflare tunnel credentials, or OAuth state.
- Prefer one authenticated gateway for a fleet instead of sharing one bearer credential across unrelated machines.
- Review public exposure with `loopback doctor` and `loopback status`.

Report security issues privately to the repository maintainers rather than opening an issue containing credentials or exploit details.
