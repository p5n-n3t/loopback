"""Exercise real discovery/verification against separate containers, without mocks."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/src/runtime")
import node_agent

root = Path.home() / "network-observations"
root.mkdir()
config = Path.home() / ".cursor/mcp.json"
config.parent.mkdir(exist_ok=True)
expected = {"mcp": "mcp_verified", "auth": "auth_required", "redirect": "redirect_requires_review", "ordinary": "unverified", "offline": "unavailable"}
config.write_text(json.dumps({"mcpServers": {name: {"url": f"http://{name}:8765/mcp"} for name in expected}}))
results = []
for name, state in expected.items():
    url = f"http://{name}:8765/mcp"
    observed = node_agent.verify_mcp_candidate(root, url)
    assert observed["state"] == state, (name, observed)
    results.append({"case": name, "expected": state, "observed": observed["state"], "outcome": "PASS"})
snapshot = node_agent.discover(root)
assert len(snapshot["mcp_endpoints"]) == 5
assert len(snapshot["mcp_verifications"]) == 5
assert not any("command" in e or "headers" in e for e in snapshot["mcp_endpoints"])
print(
    json.dumps(
        {"observed_at": time.time(), "kind": "real-container-network", "checks": results, "declared_endpoints": 5, "verification_records": 5, "outcome": "PASS"}
    )
)
