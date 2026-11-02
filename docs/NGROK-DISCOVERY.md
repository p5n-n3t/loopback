# Existing ngrok route discovery

A worker can read an already-running ngrok inspection API when its owner explicitly configures it. Create `<worker-root>/discovery-providers.json` with mode 0600:

```json
{"ngrok_api":"http://127.0.0.1:4040"}
```

The default worker root is `~/.local/share/loopback-worker`; the controller worker root is `<Loopback config>/worker`. Only HTTP on literal loopback addresses is accepted. Discovery never enables an inspection endpoint, starts ngrok, adopts routes, follows redirects, or returns upstream credentials/query strings. Up to 100 routes and a 1 MiB response are accepted. An unavailable API remains unavailable, not an empty healthy inventory.

Routes appear as external public observations. A matching listener is an observation, not proof of MCP readiness. Container-local loopback still requires separate namespace correlation. This adapter is fixture-tested; live acceptance requires an authorized ngrok agent with its inspection API already enabled.

Tests cover disabled defaults, rejection of non-loopback API addresses, credential/query redaction and inventory classification. A live ngrok account is not needed for these tests, and their success does not establish live ngrok tunnel availability.
