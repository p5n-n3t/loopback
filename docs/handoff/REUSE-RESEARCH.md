# Reuse research: Loopback host discovery, workers, inventory, and dashboard

**Purpose.** Identify existing building blocks for the current Python Loopback architecture. This is a research handoff, not an implementation plan or license opinion. Repositories, docs, and license files below are primary project sources on GitHub. License conclusions describe the linked upstream files as observed on 2026-10-31; review transitive dependencies and exact release tags before redistribution.

## Existing Loopback seams

`runtime/ssh_targets.py` already manages manually added SSH destinations, resolves/probes them through system OpenSSH, and stores identity-file paths without reading key contents. `runtime/discovery.py` and `runtime/node_agent.py` implement read-only discovery of Tailscale routes, cloudflared processes, listeners, and MCP candidates. `runtime/workers.py` owns the SSH worker connection and bootstrap path. `runtime/topology.py` and `runtime/static/topology.js` provide the topology view. Consequently, a new SSH stack or discovery framework would overlap existing code and expand the trust surface.

## Candidates

### 1. Tailscale CLI JSON and CLI design guidance

- **Repository:** [tailscale/tailscale](https://github.com/tailscale/tailscale)
- **License file:** [LICENSE](https://github.com/tailscale/tailscale/blob/main/LICENSE) (BSD 3-Clause text observed in the upstream file).
- **Useful surface:** `tailscale status --json`, especially peer host name, DNS name, addresses, OS, and online state. The project’s [CLI output guidelines](https://github.com/tailscale/tailscale/blob/main/docs/cli.md) say stable GA JSON should remain backward compatible, allow added fields, and require consumers to tolerate unknown fields.
- **Fit:** Use the installed CLI as an optional first discovery stage. Read-only discovery can populate visible unmanaged-host candidates automatically. Keep registration, adoption, installation, and other mutations behind explicit user selection. Continue to merge parsed SSH aliases and manual endpoints with discovered candidates.
- **Cost / risk:** No new dependency, but subprocess timeout, binary absence, daemon-down, malformed/older JSON, and platforms without Tailscale need explicit unavailable states. CLI JSON is more suitable than importing Tailscale’s Go packages into Python. Its stable-output guidance is a useful contract, not a guarantee that every status field has identical semantics forever.
- **Recommendation:** **Use** CLI JSON and its compatibility rule; **do not copy** Go implementation code or assume peer visibility grants authorization.

### 2. AsyncSSH

- **Repository:** [ronf/asyncssh](https://github.com/ronf/asyncssh)
- **License file:** [LICENSE](https://github.com/ronf/asyncssh/blob/develop/LICENSE) (EPL-2.0, with GPL-2.0-or-later secondary licensing under stated conditions).
- **Useful surface:** `asyncssh.connect()`, connection `run()`, SFTP/SCP, and local/remote TCP and Unix-socket forwarding. The project supports OpenSSH-style config and known-hosts files.
- **Fit:** A possible future Python-native backend if Loopback needs cancellable concurrent SSH operations, structured SSH errors, or SSH port forwarding without subprocesses.
- **Cost / risk:** Adds `asyncssh` and `cryptography`, requires integrating key/agent/config and host-key policy with current service-account semantics, and changes process/event-loop interactions. Existing code uses OpenSSH BatchMode and already classifies probe errors; replacing it would be meaningful maintenance work. EPL/GPL dual license requires a deliberate dependency review.
- **Recommendation:** **Protocol/API inspiration now; defer as a runtime dependency** until a concrete SSH capability cannot be supported cleanly through OpenSSH.

### 3. Fabric

- **Repository:** [fabric/fabric](https://github.com/fabric/fabric)
- **License file:** [LICENSE](https://github.com/fabric/fabric/blob/main/LICENSE) (BSD-2-Clause, confirmed by the project repository license label).
- **Useful surface:** `fabric.Connection(host, user=..., connect_kwargs=...)`, `.run()`, `.sudo()`, and its Invoke task model. Fabric is a Python library for remote execution and deployment, built on Paramiko.
- **Fit:** It can inform a deliberately bounded host-bootstrap workflow, especially command result handling and a user-facing sequence of remote setup steps.
- **Cost / risk:** Adds Fabric, Invoke, and Paramiko behavior and configuration on top of a service that already invokes system SSH. It may not honor every local OpenSSH behavior identically. The package is designed for general deployment orchestration, which is wider than Loopback’s small worker install/update handshake.
- **Recommendation:** **Use as a design reference only** for bootstrapping; keep the current worker protocol and OpenSSH transport unless a documented feature gap justifies adoption.

### 4. pyinfra

- **Repository:** [pyinfra-dev/pyinfra](https://github.com/pyinfra-dev/pyinfra)
- **License file:** [LICENSE.md](https://github.com/pyinfra-dev/pyinfra/blob/3.x/LICENSE.md) (MIT text observed).
- **Useful surface:** Inventory targets, ad-hoc `exec`, declarative operations, and connectors for SSH, local hosts, and Docker. Its docs/examples show Python inventory files plus reusable operations.
- **Fit:** A reference for idempotent first-time provisioning, where a selected host needs packages, service setup, or a sequence beyond Loopback’s worker bootstrap.
- **Cost / risk:** Introduces a general configuration-management model, connector and operation abstractions, and nontrivial dependency surface. It can execute broad remote changes, so embedding it in the always-on gateway would need strict scope, confirmation, and policy integration. This is more than the current bootstrap task requires.
- **Recommendation:** **Borrow ideas** such as declarative/idempotent steps for a separate opt-in provisioning feature; **do not embed** pyinfra into the current worker path.

### 5. cloudflared

- **Repository:** [cloudflare/cloudflared](https://github.com/cloudflare/cloudflared)
- **License file:** [LICENSE](https://github.com/cloudflare/cloudflared/blob/master/LICENSE) (Apache-2.0, confirmed from the file and upstream build metadata).
- **Useful surface:** The `cloudflared tunnel` CLI and locally managed tunnel configuration with ordered ingress rules. The [configuration source](https://github.com/cloudflare/cloudflared/blob/master/config/configuration.go) exposes tunnel IDs, ingress rules, and related configuration fields.
- **Fit:** Loopback already inventories running cloudflared processes and their readable config files as existing exposure. This is specifically Cloudflare Tunnel, which proxies traffic to public hostnames. It is not Cloudflare Mesh and does not implement private mesh networking.
- **Cost / risk:** Do not import the Go project into Python. A setup flow would need an independently reviewed enrollment/authentication path, lifecycle ownership, route conflict checks, and credential-safe state handling. Cloudflare account credentials are intentionally out of scope here.
- **Recommendation:** **Use CLI/config behavior as protocol inspiration for Cloudflare Tunnel only**; retain read-only inventory for unmanaged tunnels. Keep Cloudflare Mesh setup as a separate opt-in product path using the Cloudflare One Client (`cloudflare-warp` and `warp-cli`), not `cloudflared`. This research does not handle enrollment credentials.

### 6. ngrok Python SDK (repository identity verified)

- **Repository:** [ngrok/ngrok-python](https://github.com/ngrok/ngrok-python) — this is the active Python SDK repository; the similarly named `ngrok-api-python` was not selected as the candidate.
- **License files:** [LICENSE-APACHE](https://github.com/ngrok/ngrok-python/blob/main/LICENSE-APACHE) and [LICENSE-MIT](https://github.com/ngrok/ngrok-python/blob/main/LICENSE-MIT). The repository presents both license files; GitHub’s repository page reports Apache-2.0 and MIT.
- **Useful surface:** `ngrok-python` embeds secure ingress into Python apps, offers an ASGI runner, and provides HTTP/TCP/TLS listeners and traffic-policy configuration in its README/examples.
- **Fit:** Useful comparison point for an opt-in tunnel-provider interface and for thinking about listener lifetime/reporting. Loopback’s current design treats ingress as a separate layer and inventories externally owned routes without taking control.
- **Cost / risk:** Includes a Rust/PyO3 component and prebuilt platform artifacts. It changes how ingress is hosted and introduces vendor-specific account setup and lifecycle concerns. It does not solve general host discovery or worker bootstrap.
- **Recommendation:** **Protocol inspiration only**; do not add the SDK to the baseline install. Extend the existing CLI provider where necessary. Store credentials through secure references.

### 7. Official MCP Python SDK

- **Repository:** [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)
- **License file:** [LICENSE](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) (MIT, confirmed from the project file).
- **Useful surface:** MCP Python server/client APIs, Streamable HTTP transport, typed tool schemas, and server authorization integration. Loopback already uses this SDK, so the relevant value is its current upstream examples and public API rather than another package.
- **Fit:** Reuse the existing SDK integration for reporting discovered hosts and inventories as MCP resources/tools. Keep discovery code behind current service modules and enforce policy at the existing tool boundary.
- **Cost / risk:** No additional dependency if Loopback’s installed SDK supports the needed API. The project’s v2 API changed transport imports/signatures, so match Loopback’s pinned version rather than copying current-main examples blindly.
- **Recommendation:** **Reuse the already-installed SDK and its version-matched APIs**; do not reimplement MCP transport or introduce a separate protocol server.

### 8. Tabulator

- **Repository:** [tabulator-tables/tabulator](https://github.com/tabulator-tables/tabulator) (the former `olifolkerd/tabulator` URL redirects here).
- **License file:** [LICENSE](https://github.com/tabulator-tables/tabulator/blob/master/LICENSE) (MIT file observed).
- **Useful surface:** Browser-side data tables with sorting, filtering, pagination, row formatters, and data refresh APIs.
- **Fit:** A candidate for dense worker, route, listener, and probe-history tables if the current vanilla JavaScript dashboard needs more interaction than native DOM tables. It does not require adopting React.
- **Cost / risk:** Adds a sizeable table library, CSS/theme behavior, and an asset/versioning step. Existing dashboard is lightweight static JS; a library is justified only if filtering, column handling, or larger inventories are real requirements.
- **Recommendation:** **Adopt selectively** for a specific dense table only after measuring against native tables; otherwise take interaction and layout cues.

### 9. vis-network

- **Repository:** [visjs/vis-network](https://github.com/visjs/vis-network)
- **License file:** [LICENSE-MIT](https://github.com/visjs/vis-network/blob/master/LICENSE-MIT) (MIT; the repo also has a separately identified Apache license file, so review bundled package notices for the chosen release).
- **Useful surface:** Browser graph visualization with node/edge datasets, automatic layout, selection, and event hooks.
- **Fit:** Can inform or render the Loopback topology graph where nodes and links are interactive. The present topology module should remain the source of graph data and policy semantics.
- **Cost / risk:** Adds a visualization dependency and its layout/interaction model; highly connected or changing graphs can create visual noise. Check the exact distributable’s notices and license files before bundling.
- **Recommendation:** **Use as a prototype/library candidate** if topology interaction is a product requirement; for a compact static topology, keep the custom renderer and borrow only visual conventions.

## Comparable applications and workflow references

These projects are useful to compare complete workflows and data models. Their scope is much larger than Loopback’s single-process MCP server, so reuse their patterns and public contracts instead of importing a whole control plane.

### 10. Cockpit

- **Repository:** [cockpit-project/cockpit](https://github.com/cockpit-project/cockpit), observed default branch `main`, tip [f00c23a2b067d0dbdc63140867e1887b84115655](https://github.com/cockpit-project/cockpit/commit/f00c23a2b067d0dbdc63140867e1887b84115655) on 2026-10-31.
- **License documents:** the upstream README lists LGPL-2.1-or-later, GPL-3.0-or-later, BSD-3-Clause, CC-BY-SA-3.0, and MIT across the project. Example upstream text: [LICENSES/GPL-3.0.txt](https://github.com/cockpit-project/cockpit/blob/main/LICENSES/GPL-3.0.txt). Treat each file and asset as separately licensed; the repository is not uniformly permissive.
- **Useful pattern:** Cockpit’s [remote SSH login flow](https://github.com/cockpit-project/cockpit/blob/main/doc/authentication.md) is a strong reference for host-key verification, unknown versus changed keys, interactive authentication prompts, and starting a remote bridge over SSH. The [SSH connection helper](https://github.com/cockpit-project/cockpit/blob/main/pkg/lib/cockpit-connect-ssh.tsx) shows how connection setup can stay separate from the product’s remote-operation protocol.
- **Fit / cost:** Read its staged auth and host-key UX for Loopback’s OpenSSH aliases and manual targets. Cockpit includes a browser UI, system daemons, bridge protocol, Linux-service integrations, and remote session lifecycle. Importing modules would pull in unrelated assumptions and licensing review; copying copyleft source needs a compatibility assessment.
- **Recommendation:** **Use workflow and protocol inspiration only. Do not copy Cockpit code into Loopback without file-specific license and compatibility review.**

### 11. MeshCentral

- **Repository:** [Ylianst/MeshCentral](https://github.com/Ylianst/MeshCentral), observed default branch `master`, tip [adce1ec488dc884ce947ea4e4808e6a520609b17](https://github.com/Ylianst/MeshCentral/commit/adce1ec488dc884ce947ea4e4808e6a520609b17) on 2026-10-31.
- **License document:** [LICENSE](https://github.com/Ylianst/MeshCentral/blob/master/LICENSE) (Apache-2.0; its README also states Apache 2.0).
- **Useful pattern:** MeshCentral models device groups, enrollable agents, inventory, connectivity, and remote capabilities. The root [README](https://github.com/Ylianst/MeshCentral/blob/master/readme.md) describes the server/agent relationship and links a design/architecture guide. `meshctrl.js` is the upstream CLI surface for management operations.
- **Fit / cost:** Compare its device enrollment lifecycle and clear distinction between host record, agent, and available capabilities. It is a Node.js remote-management control plane with an installed persistent agent, database, web UI, remote desktop/terminal/file transfer, relay, and certificate trust model. That is a substantial runtime and security surface compared with Loopback’s narrowly scoped SSH worker.
- **Recommendation:** **Use device and enrollment workflow inspiration; do not adopt the full MeshCentral server or agent architecture.** Review file and third-party asset licenses before copying any implementation.

### 12. IBM MCP Context Forge

- **Repository identity:** [IBM/mcp-context-forge](https://github.com/IBM/mcp-context-forge) is the current repository under the IBM organization (observed default branch `main`, tip [a75ff7222d6b3d6df010cab340bc7e5a4e854c94](https://github.com/IBM/mcp-context-forge/commit/a75ff7222d6b3d6df010cab340bc7e5a4e854c94) on 2026-10-31). Its current name is MCP Context Forge; older references may use “MCP Gateway.”
- **License document:** [LICENSE](https://github.com/IBM/mcp-context-forge/blob/main/LICENSE) (Apache-2.0, corroborated by the GitHub repository license label).
- **Useful pattern:** Its [features overview](https://github.com/IBM/mcp-context-forge/blob/main/docs/docs/overview/features.md) shows how a registry exposes server/tool/resource catalogs, policy and observability controls, and an admin UI. This is a close reference for clear inventory rows, status, gateway provenance, and admin-facing discovery.
- **Fit / cost:** Loopback already has MCP tool/resource contracts, policy gates, and a dashboard API. Context Forge is a separate full gateway with federation, persistence, caching, auth, plugins, and a substantial admin surface. Importing it would duplicate Loopback’s server and authorization boundaries instead of reducing implementation cost.
- **Recommendation:** **Use naming, catalog, and status presentation patterns only.** Reuse MCP protocol APIs already present in Loopback rather than adding a second gateway framework.

### 13. Vercel Agent Browser

- **Repository:** [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser), observed default branch `main`, tip [5868f7c05020e047c00e68e4abf9253cce864f41](https://github.com/vercel-labs/agent-browser/commit/5868f7c05020e047c00e68e4abf9253cce864f41) on 2026-10-31.
- **License document:** [LICENSE](https://github.com/vercel-labs/agent-browser/blob/main/LICENSE) (Apache-2.0).
- **Useful surface:** A CLI wraps Chromium automation with accessibility snapshots, compact stable element refs, semantic interaction, screenshots, tabs, and machine-readable JSON. Its [README](https://github.com/vercel-labs/agent-browser/blob/main/README.md) documents install/runtime requirements; its snapshot/ref workflow can reduce fragile selector-based dashboard checks.
- **Fit / cost:** `runtime/browser.py` already invokes `agent-browser` for Loopback's browser tools. Treat it as an optional runtime dependency and fix its installation, health checks and per-principal session boundary. It is not merely dashboard test tooling.
- **Cost / risk:** Requires a native CLI and browser installation, so it would add setup weight if made mandatory. Upstream issues document some remote-CDP snapshot limitations; pin and validate a release before relying on it in automation.
- **Recommendation:** **Keep a pinned opt-in runtime integration**, with a verified compatible engine, isolated profiles, lifecycle limits and URL policy. Keep the core headless install small. Do not install a browser merely to capture a Lightsprint preview screenshot.

### Cloudflare Mesh is separate from Cloudflare Tunnel

Cloudflare Mesh setup uses the Cloudflare One Client, packaged as `cloudflare-warp`, and its `warp-cli` interface. The official [Linux Mesh guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-mesh/guides/run-mesh-on-linux/) documents installation and `warp-cli status`; the official [headless Linux deployment guide](https://developers.cloudflare.com/cloudflare-one/tutorials/deploy-client-headless-linux/) describes a separate device enrollment workflow. This is vendor-managed software, not a Python library to transplant. **Implementation scope:** include Mesh install/config as a delivery requirement, with the feature opt-in. Use [CLOUDFLARE-MESH.md](CLOUDFLARE-MESH.md) for server-node enrollment, route coexistence and safe credential references. Keep secret values out of plain configuration, logs, and research artifacts.

## Suggested direction

1. Populate an unmanaged candidate inventory from Tailscale peers and parsed OpenSSH aliases, with manual entry as another source. Keep discovery read-only; require selection before adopting a host, installing a worker, or mutating remote state. Reuse the current `ssh_targets.py` registry, masking secrets and keeping key material outside Loopback.
2. Keep worker bootstrap narrow and explicit. Continue using the worker installer/protocol and system OpenSSH. Use Fabric or pyinfra as references for idempotent steps, not as embedded frameworks.
3. Keep tunnel/listener/MCP inventory read-only and provenance-aware. The existing discovery module already distinguishes managed, external, unknown, unreachable, and excluded cases. Treat Cloudflare Tunnel (`cloudflared`) inventory and Cloudflare Mesh (`cloudflare-warp` / `warp-cli`) setup as separate provider surfaces. Keep Mesh installation opt-in and enrollment credential-safe.
4. Keep dashboard dependencies small. Reuse the current API/data model; consider Tabulator only for dense tables and vis-network only for interactive topology. Vanilla JS remains a sound default for the current surface.
5. For every selected dependency, pin a release and audit the exact release’s license files and transitive dependencies. The links above follow project default branches and can move.

## Source links checked

- Tailscale [CLI JSON guidelines](https://github.com/tailscale/tailscale/blob/main/docs/cli.md), [status implementation](https://github.com/tailscale/tailscale/blob/main/cmd/tailscale/cli/status.go), and [license](https://github.com/tailscale/tailscale/blob/main/LICENSE).
- AsyncSSH [repository and API examples](https://github.com/ronf/asyncssh) and [license](https://github.com/ronf/asyncssh/blob/develop/LICENSE).
- Fabric [repository](https://github.com/fabric/fabric) and [license](https://github.com/fabric/fabric/blob/main/LICENSE).
- pyinfra [repository/docs](https://github.com/pyinfra-dev/pyinfra) and [license](https://github.com/pyinfra-dev/pyinfra/blob/3.x/LICENSE.md).
- cloudflared [repository](https://github.com/cloudflare/cloudflared), [configuration source](https://github.com/cloudflare/cloudflared/blob/master/config/configuration.go), and [license](https://github.com/cloudflare/cloudflared/blob/master/LICENSE).
- ngrok [Python repository](https://github.com/ngrok/ngrok-python), [Apache license](https://github.com/ngrok/ngrok-python/blob/main/LICENSE-APACHE), and [MIT license](https://github.com/ngrok/ngrok-python/blob/main/LICENSE-MIT).
- MCP Python SDK [repository](https://github.com/modelcontextprotocol/python-sdk), [Streamable HTTP client docs](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md), and [license](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE).
- Tabulator [repository](https://github.com/tabulator-tables/tabulator) and [license](https://github.com/tabulator-tables/tabulator/blob/master/LICENSE).
- vis-network [repository](https://github.com/visjs/vis-network) and [MIT license file](https://github.com/visjs/vis-network/blob/master/LICENSE-MIT).
- Cockpit [repository](https://github.com/cockpit-project/cockpit), [remote SSH authentication docs](https://github.com/cockpit-project/cockpit/blob/main/doc/authentication.md), and [example GPL-3.0 license file](https://github.com/cockpit-project/cockpit/blob/main/LICENSES/GPL-3.0.txt).
- MeshCentral [repository](https://github.com/Ylianst/MeshCentral) and [Apache-2.0 license](https://github.com/Ylianst/MeshCentral/blob/master/LICENSE).
- IBM MCP Context Forge [repository](https://github.com/IBM/mcp-context-forge), [feature overview](https://github.com/IBM/mcp-context-forge/blob/main/docs/docs/overview/features.md), and [LICENSE](https://github.com/IBM/mcp-context-forge/blob/main/LICENSE).
- Vercel Agent Browser [repository](https://github.com/vercel-labs/agent-browser), [README](https://github.com/vercel-labs/agent-browser/blob/main/README.md), and [LICENSE](https://github.com/vercel-labs/agent-browser/blob/main/LICENSE).
- Cloudflare Mesh [Linux setup guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-mesh/guides/run-mesh-on-linux/) and [headless client deployment guide](https://developers.cloudflare.com/cloudflare-one/tutorials/deploy-client-headless-linux/). No public Cloudflare One Client source repository was identified in this research.
