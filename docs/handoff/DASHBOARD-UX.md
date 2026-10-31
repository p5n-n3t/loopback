# Dashboard polish and operation requirements

Improve the existing vanilla JavaScript dashboard. Preserve its working routes and backend contracts until a migration is necessary.
Use [EVIDENCE.md](EVIDENCE.md) as the visual baseline. Existing images are context, not proof of the new design.

## 1. Information hierarchy and density

Use a compact status header, a small set of meaningful summaries, and searchable working tables.
Avoid oversized headings, large blank cards, repeated explanatory paragraphs and clipped action names.
Use consistent spacing, typography, borders, icon meaning, contrast and loading states.
Keep page actions next to the relevant table or detail panel. Do not scatter connection repair across unrelated settings pages.
Prefer progressive disclosure for advanced details. Keep the current stage and next useful action visible.
Use consistent names for Host, SSH access, Worker, MCP endpoint, Tunnel, Route and Primary ingress.
Do not call all these objects nodes or display one ambiguous green dot for their health.

Keep the graph optional. A table must support keyboard operation, filters, sorting and direct access to full details.
Measure native tables before adding Tabulator. Avoid a frontend-framework migration for styling alone.
Source candidates and licenses are in [REUSE-RESEARCH.md](REUSE-RESEARCH.md).

## 2. Approval and denial section

The current `Approval` record stores a fingerprint and summary rather than the full structured action.
Fixing truncated details therefore needs backend work as well as CSS.
Store a safe action descriptor and a separate redacted presentation, with retention and principal ownership.
Do not expose secrets in a full-command viewer or persist sensitive arguments unnecessarily.

| Area | Required behavior |
| --- | --- |
| Queue row | Show action category, target, requesting principal/client, risk, request time and status. Keep buttons readable. |
| Detail panel | Show the complete safe command or structured action, working directory, target host/path, requested privilege and expiry. |
| Long values | Wrap or provide a scrollable preformatted block and Copy action. Do not rely on hover tooltips or ellipsis for safety decisions. |
| Redaction | Mark hidden values explicitly. Keep a clear relationship between the redacted view and the fingerprint-bound action. |
| Approval | Explain one-time or time-limited scope and expiry. Bind the decision to the existing action fingerprint, principal and target. |
| Denial | Show a useful reason, category, timestamp and safe retry/remediation. Separate policy denial, missing OAuth scope and provider refusal. |
| History | Filter pending/approved/denied/expired and inspect the reason without opening several nested cards. |
| Changed action | Require a new decision if command, arguments, cwd, target or authorization context changes. |
| Concurrency | Resolve only the selected request. Handle duplicate clicks, stale decisions and another administrator's action. |

Do not make approval easier by hiding the actual action or automatically extending a grant.
Do not claim a dashboard setting can disable OpenAI or Anthropic safety decisions.
Use accurate tool annotations and explicit consent instead of disguising write or execution tools.

## 3. Dynamic discovery screen

Start with local inventory and automatically listed unmanaged candidates.
Show the source and last observation, then stage-specific status and a next action.
Provide scope/exclusion controls, Scan/Cancel, targeted Retry and a concise progress summary.
Support multi-select for approved probes and enrollment plans with per-host results.
Show new, existing, excluded, offline, stale, authentication-required, enrollment-required and failed states distinctly.
An empty result explains whether no peers exist, the provider is missing, permissions restrict inventory, or the scan has not run.

Use a host drawer with sections for connectivity, SSH, worker, services, MCP endpoints, tunnels, routes and evidence.
Show domains and ports in full with copy controls. Associate every route with its source, target and freshness.
Distinguish private/public/unknown exposure and managed/external ownership.
Provide one primary-ingress selector for this Loopback installation with a preview and rollback details.
The selector must preserve unrelated tunnels and endpoints.

Mesh setup must have its own guided stages and coexistence warning when required.
Do not hide a failed SSH login behind a successful Mesh or Tailscale status.
Provide manual entry when inventory is empty or partial, without making manual entry the only discovery mechanism.

## 4. Capability, authentication and support surfaces

For browser, desktop, fleet and tunnels, distinguish enabled, dependency installed, permission granted, reachable and functionally verified.
Show the last test time and a precise remediation command or setup action.
Show actual sandbox enforcement separately from configured policy and `sandbox_required`.
Keep local OS enforcement distinct from raw SSH gateway checks and optional remote enforcement.
Present the current principal's scopes alongside policy constraints and approval mode.
Make clear that enabling a tool in policy does not update an old OAuth grant.
Keep revoke and legacy migration controls discoverable without automatically breaking active clients.
Offer an inspectable redacted support export. Never upload it silently.
Support bundles must exclude commands with secrets, tokens, personal screen data and credential paths where unnecessary.

## 5. Interaction and accessibility acceptance

- At 1280-pixel desktop width, dense rows remain readable without oversized empty panels.
- At about 390-pixel mobile width, essential actions and full details remain accessible without page-wide horizontal overflow.
- Long hostnames, commands, paths, denial reasons, Unicode and 200-row inventories remain usable.
- Keyboard users can open details, review a decision, activate actions and return focus predictably.
- Labels, focus indicators, status text and error messages do not depend on color alone.
- Empty, loading, partial, offline, stale, no-permission and server-error states have separate content.
- Pending actions prevent accidental duplicate submission and display a recoverable failure.
- Copy controls provide feedback without exposing secrets or producing a long transient-only message.
- Tables use stable row identity, so refresh does not move keyboard focus or select another host.
- Automated checks assert content and authorization. Small representative screenshots prove changed layout states.

Use the minimum new screenshots that establish the diff: approval detail, discovery results/stages, and one representative narrow layout.
Combine compatible states in one planned capture batch. Reuse unchanged images from the evidence manifest.
Use a short video only if stage motion or a transient state is itself the evidence.
Follow the current `/proof` rules. Screenshot efficiency does not excuse missing or stale verification.
