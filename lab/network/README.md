# Real network and runtime lifecycle lab

Build `lab/oracle/Dockerfile` from the repository root. It copies source into an unprivileged Python 3.11 container. `.dockerignore` excludes local recovery and agent state.

On an authorized Docker host:

```sh
python3 lab/network/run.py --image loopback-lab:coordinated --output network-result.json
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges loopback-lab:coordinated python /src/lab/network/lifecycle.py
```

The network runner creates a unique internal Docker bridge with five separate fixture containers and an observer. It publishes no ports, mounts no host paths, injects no provider credentials, and tests real TCP/HTTP requests. Cases cover valid MCP initialization, HTTP 401, an untrusted redirect (never followed), non-MCP JSON, and an offline listener. It records expected/observed states and removes only resources with its exact ownership label, including on failure. The output JSON records residual resources and exits nonzero on a failed case or incomplete cleanup.

The lifecycle test uses actual `install`, `upgrade`, and `rollback` commands. It uses preinstalled dependencies so it can run without external networking. A new runtime marker proves code changed on upgrade and disappeared on rollback. Machine-token and signing-key hashes are compared in memory and never printed. Port/config preservation and listener shutdown are asserted. This is a same-version code-change test, not proof of cross-release migrations. The separate fresh-install smoke harness covers real virtualenv dependency installation.

These containers establish Linux user-space and selected protocol behavior. They do not certify full VM/service-manager/reboot scenarios, macOS, Windows, live Cloudflare Mesh enrollment, live provider network setup, or every discovery scenario. `lab/oracle/topology-lab.py` is additional fixture coverage; it does not create a network.
