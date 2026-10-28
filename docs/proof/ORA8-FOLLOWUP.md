# Live follow-up proof

Source revision: `9c19148262ada84be1f5d1345afbf8bb43ef9f43`. Base: `7d1b2d3`. Counts exclude blank/comment lines and Python docstrings.
The [real ora8 result](ORA8-LAB-RESULT-2026-10-28.json) supersedes the earlier identity blocker.

```mermaid
---
title: Discovery safety and ora8 lab proof — 9c19148
---
flowchart TB
classDef plus fill:#dcfce7,stroke:#15803d
classDef minus fill:#fee2e2,stroke:#b91c1c
classDef wait fill:#fff7d6,stroke:#b7791f
subgraph discovery[Read-only discovery]
a["Existing cloudflared process arguments"] -->|unchanged input| old["- Per-argument regex can expose split credential values"]:::minus
old -.->|replaced| b["+ Token-aware split/inline redaction; preserve safe config path<br/><small><u>runtime/node_agent.py:315</u><br/><b><font color="#15803d">+22</font></b> <b><font color="#b91c1c">-2</font></b></small>"]:::plus
b -->|~ sanitized values| inventory["Existing worker inventory and classification"]
inventory -->|proof| test["8 discovery tests pass. Fake /proc covers secrets.<br/>Real collectors succeed on six registered hosts; ora7 times out."]
end
subgraph lab[Ora8 isolated installation validation]
guard["+ Require ora8 + dedicated nonroot account; isolate XDG, port and process ownership<br/><small><u>scripts/remote-lab-smoke.sh</u><br/><b><font color="#15803d">+229</font></b> <b><font color="#b91c1c">-0</font></b></small>"]:::plus -->|+ allowed account| install["+ Fresh and repeat install; OAuth PKCE/refresh/MCP;<br/>recovery; upgrade/rollback; private cleanup"]:::plus
install -->|+ redacted result only| result["+ PASS labels and private result JSON"]:::plus
guard -->|+ other account| refuse["+ Refuse before installation. Local guard and syntax checks pass."]:::plus
install -->|real ora8 run| proven["13 checks pass as loopback-lab at 9c19148.<br/>Listener closes and temporary checkout is removed."]:::plus
install -.->|outside lab scope| blocked["Desktop rollout, permanent worker registration,<br/>real browser clients and live ngrok remain unverified."]:::wait
end
```

The ora8 lab flow has real runtime proof. All 13 checks pass through authenticated Tailscale SSH as the dedicated account.
The port regression test rejects a live listener and accepts closed TIME_WAIT connections.
The saved JSON contains check names and cleanup state. It contains no credentials, hashes, cookies, or enrollment links.
Real HTTP checks in the isolated Lightsprint gateway pass all three public/Basic/form client flows, including PKCE, refresh, mixed-grant narrowing, and nonempty MCP tool results.
The desktop still advertises only public OAuth clients. It is not updated.
