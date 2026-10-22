# Loopback Agent Skills

This directory holds the Agent Skills bundled with Loopback. Each skill is a
self-contained `SKILL.md` with YAML frontmatter (`name`, `description`) that
an agent harness uses to decide when to load it — the body stays short and
links out to the full documentation in `docs/` rather than repeating it.

| Skill | Use it for |
|---|---|
| [`loopback-core`](loopback-core/SKILL.md) | Filesystem, shell, jobs, terminals, documents on one connected machine. |
| [`loopback-fleet`](loopback-fleet/SKILL.md) | Routing to other registered Loopback nodes or SSH targets; topology/health. |
| [`loopback-admin`](loopback-admin/SKILL.md) | Authentication, policy profiles, ingress, backup/upgrade administration. |
| [`loopback-troubleshooting`](loopback-troubleshooting/SKILL.md) | Diagnosing a broken or misbehaving Loopback server. |
| [`loopback-mcp-development`](loopback-mcp-development/SKILL.md) | Building MCP/OAuth clients and integrations against Loopback's HTTP surface. |

## Packaging

`scripts/build_plugin.py` bundles this whole directory into the Agent Plugin
archive it builds (see `docs/SKILLS.md` and `docs/PLUGIN.md`). A skill added
here is picked up automatically on the next build; nothing else needs to
reference it by name.

## Conventions these skills follow

- No secrets, no real hostnames/usernames, no environment-specific paths.
- Never instruct working around Loopback's own server-side authorization —
  policy and approval decisions belong to the machine owner.
- Each skill assumes the others exist and links to them instead of
  duplicating their content.

`scripts/check_skills.py` enforces the structural rules (frontmatter name
matches the directory, description length, size limit, forbidden content) —
run it after changing anything under this directory.
