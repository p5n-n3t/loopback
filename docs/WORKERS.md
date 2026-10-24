# Workers and independent MCP services

The controller stays on your computer. A worker is an SSH-controlled runtime on
another Linux machine. It has no public management API and adds no filesystem or
shell tools to the MCP server you choose. MCP requests go directly to the worker;
they do not pass through an SSH connection to your computer.

## From the dashboard

1. Add a machine under **SSH config** using an account that can run Python 3.11+
   and create a virtual environment. For an existing SSH alias, tick **Use SSH alias settings** to preserve its host, port and user settings. Keys stay local.
2. In **Fleet**, choose **Install / update**. Code and a dedicated Python virtual
   environment go under `~/.local/share/loopback-worker`. Dependency installation
   runs in the background; inspect `setup.log` there if it fails.
3. Open **Services & tunnels**, choose that worker and a free loopback port.
4. For HTTP, enter the MCP URL as reachable **from the worker**. For stdio, enter
   a JSON array such as `["npx", "-y", "@modelcontextprotocol/server-filesystem",
   "/srv/project"]`. Use a dedicated OS account and restrict that server's own
   permissions. Worker commands run as its SSH user, outside the controller's
   filesystem policy. Do not put credentials in command arguments.
5. Choose loopback-only, Tailscale Serve, Tailscale Funnel, or Cloudflare. Save,
   then explicitly press **Start**. Saving a definition has no exposure side effect.
6. Press **Connect** to obtain a ten-minute enrollment code. Set the service's
   own password at its enrollment URL. Add its `/mcp` URL in ChatGPT or another
   OAuth-capable MCP client and authorize it. The worker reuses Loopback's OAuth
   2.1 server, PKCE, refresh rotation, revocation and audience binding. It requires
   `loopback:exec` because an arbitrary upstream can execute tools. The controller's
   machine token is never accepted by the worker.

For a loopback-only service on a VM, forward its port first:

```sh
ssh -N -L 3031:127.0.0.1:3031 my-worker
# Open http://127.0.0.1:3031/admin/enroll; the MCP URL is /mcp.
```

Each binding has its own process, OAuth database, signing key, listener and
credential enrollment. Disabling local shell access does not stop remote workers.
Stop a service before editing, moving or deleting it. Stopping an already crashed
service also cleans up its owned tunnel. A failed start leaves its desired state
stopped so you can fix the error and retry.

## Tunnels and existing routes

There is no Loopback count cap. Ports, memory, provider quotas and the operating
system still apply. Tailscale Funnel supports HTTPS ports **443, 8443 and 10000**
per machine. Use another worker or Cloudflare when those ports are occupied.

A worker refuses **any occupied Tailscale HTTPS port**, including a port with only
an existing `/mcp` handler. It never resets Serve/Funnel. Stop only removes an
exact, owned root proxy that has not changed externally. Mixed or changed handlers
are left alone and reported as a conflict. The legacy gateway's Tailscale page
has the same protection. In particular, an orchestration URL such as RotoXY's must
be left on its original port and machine.

Cloudflare uses a distinct `cloudflared` process and a **token-file path on the
worker** for each binding. Create the tunnel in Cloudflare and point its published
hostname to `http://127.0.0.1:<worker-port>`. The dashboard does not create DNS or
rewrite another tunnel's Cloudflare configuration. OAuth endpoints and `/mcp` must
all reach this same worker port. Configure each hostname before starting it.
Never share one remotely managed tunnel token across bindings with different
origins: Cloudflare distributes traffic among connectors of that same tunnel.

## Telemetry

Overview, Fleet and Topology distinguish host counters from Loopback counters:

- CPU is a percentage of total host CPU capacity, measured between samples;
  the first sample is unknown. Loopback includes managed bridges, their stdio
  children, and their Cloudflare processes. On the local controller it also
  includes the gateway process and its children.
- Memory is current process RSS (including managed children), as bytes and as a
  fraction of total host RAM. Host used RAM is separate. Summed RSS can count
  shared pages more than once.
- MCP throughput counts authenticated MCP request/response **body bytes** at the
  bridge, shown as incoming/outgoing bytes per second. It excludes TLS, HTTP
  headers, OAuth traffic and unrelated host traffic. Host traffic is a separate
  aggregate of non-loopback interface counters; stacked virtual interfaces can
  count the same packet twice.
- Heartbeat is the last successful SSH sample. An unreachable worker shows its
  last heartbeat, never an old measurement labelled live.

Overview, Fleet, Topology and service state refresh every five seconds while visible. Editing
forms are preserved. Topology shows process health separately from public
reachability; a launched process does not prove DNS, certificates or a public MCP
client connection work.

## Current boundaries

Workers currently target Linux (`/proc` supplies process ownership and metrics).
The main Loopback gateway remains cross-platform. Workers need Python 3.11+ and,
for stdio, the selected MCP server and its dependencies. Installing a worker does
not install arbitrary upstream packages or enable root access.

Processes survive a controller restart. Automatic worker/service restart after a
VM reboot is not installed by this version. Restart desired services from the
dashboard after a reboot. Worker logs are private files and should be rotated by
the host's normal log policy. StdIO proxies support tools, resources and prompts;
server-initiated sampling, elicitation and subscription forwarding are not yet
implemented. The stdio upstream session is shared within one binding; use distinct
bindings for workloads that require separate upstream state.

Public ChatGPT/Perplexity browser authorization must still be verified in the
actual client after enrollment; test calls alone do not prove that integration.
