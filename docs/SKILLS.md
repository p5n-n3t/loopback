# Agent Skills

Loopback ships five bundled [Agent Skills](https://www.anthropic.com/news/skills)
under `skills/` — Markdown instructions an MCP-capable agent can load to use
Loopback well, without you having to re-explain its conventions in every
conversation.

| Skill | Use it for |
|---|---|
| [`loopback-core`](../skills/loopback-core/SKILL.md) | Filesystem, shell, jobs, terminals, and documents on one connected machine. |
| [`loopback-fleet`](../skills/loopback-fleet/SKILL.md) | Routing to other Loopback nodes and SSH targets; reading topology. |
| [`loopback-admin`](../skills/loopback-admin/SKILL.md) | Dashboard/admin-API administration: auth, policy profiles, ingress, backups, upgrades. |
| [`loopback-troubleshooting`](../skills/loopback-troubleshooting/SKILL.md) | Diagnosing an unreachable or misbehaving Loopback server. |
| [`loopback-mcp-development`](../skills/loopback-mcp-development/SKILL.md) | Building an MCP/OAuth client or integration against Loopback's HTTP surface. |

Each skill is intentionally narrow and links to the full docs instead of
duplicating them — see [skills/README.md](../skills/README.md) for the
conventions they all follow (no secrets, no bypassing server-side
authorization, link rather than repeat).

## Using the skills directly

If your agent harness supports loading Markdown skills from a directory,
point it at `skills/` and it will pick up all five, each routed by its
`description` frontmatter.

## Packaging as an Agent Plugin

`scripts/build_plugin.py` bundles all five skills (plus `plugin.json` and
`mcp.json`) into a single zip for harnesses that install plugins rather
than loose skill directories:

```bash
python3 scripts/build_plugin.py --mcp-url https://loopback.example.com/mcp --output loopback-plugin.zip
```

The resulting archive's `plugin.json` `version` always matches the running
Loopback version (`runtime/version.py`), and `skills/` is copied verbatim —
there's no separate skills build step to keep in sync.

## Validating a skill change

```bash
python3 scripts/check_skills.py
```

Checks that there are exactly five skill directories, each `SKILL.md`'s
frontmatter `name` matches its directory, `description` is specific enough
to route on (≥40 characters), the body stays under 500 lines, and that no
skill leaks a real path/token/hostname or tells an agent to bypass
Loopback's own policy.

## See also

- [skills/README.md](../skills/README.md)
- [ARCHITECTURE.md](ARCHITECTURE.md) — where the plugin builder fits in the
  release process.
