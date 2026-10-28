# Resumed flow proof

Old: `66c4891f7029f22cdc1df0a05af13d69106f34e4` (merge-base with origin/main). New source: `564b6fc511db351444c3d21f4b881002341e2a5d`.

Counts exclude tests, comments, and Python docstrings. Files shared by steps are split by the edited source region.

## Account and consent credentials

```mermaid
---
title: Account and consent credentials — 66c4891 to 564b6fc
---
flowchart TB
classDef added fill:#dcfce7,stroke:#16a34a,color:#14532d
classDef changed fill:#fef9c3,stroke:#ca8a04,color:#713f12
classDef same fill:#f8fafc,stroke:#94a3b8,color:#334155
a["Fresh or existing installation"]:::same -->|"~ bootstrap"| b["+ Generate fresh account; preserve existing password<br/><br/><small><u>runtime/auth.py:357</u><br/><b><font color="#15803d">+39</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 b -->|"~ account login"| c["~ Require username for new accounts; keep recovery<br/><br/><small><u>runtime/auth_http.py:161</u><br/><b><font color="#15803d">+3</font></b> <b><font color="#b91c1c">-2</font></b></small> "]:::changed
 d["+ Reauthenticate and require session plus CSRF<br/><br/><small><u>runtime/admin_api.py:461</u><br/><b><font color="#15803d">+21</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added -->|"+ account update"| b
 d -->|"+ rotate consent key"| e["+ Store only the setup-key hash; keep existing grants<br/><br/><small><u>runtime/auth.py:303</u><br/><b><font color="#15803d">+23</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 e -->|"+ explicit consent"| f["~ Accept setup key or dashboard credentials; preserve PKCE<br/><br/><small><u>runtime/auth_http.py:185</u><br/><b><font color="#15803d">+5</font></b> <b><font color="#b91c1c">-4</font></b></small> "]:::changed
 c -->|"~ existing session"| f
 f -->|"unchanged OAuth exchange"| g["Scoped authorization code, token and revocation contracts"]:::same
 u["~ Account/key controls expose separate revocation semantics<br/><br/><small><u>runtime/static/app.js:846</u><br/><b><font color="#15803d">+23</font></b> <b><font color="#b91c1c">-1</font></b></small> "]:::changed -->|"+ guarded update"| d
 p["Proof: 30 auth tests and 15 OAuth tests pass. Real provider browser sign-in remains unverified."]:::same
 g --- p
```

## Stable ngrok origin and owned lifecycle

```mermaid
---
title: Stable ngrok origin and owned lifecycle — 66c4891 to 564b6fc
---
flowchart TB
classDef added fill:#dcfce7,stroke:#16a34a,color:#14532d
classDef changed fill:#fef9c3,stroke:#ca8a04,color:#713f12
classDef same fill:#f8fafc,stroke:#94a3b8,color:#334155
a["+ Validate fixed hostname and v3 config; require public confirmation<br/><br/><small><u>runtime/config.py:53</u><br/><b><font color="#15803d">+18</font></b> <b><font color="#b91c1c">-2</font></b></small><br/><small><u>runtime/fabric_api.py:318</u><br/><b><font color="#15803d">+12</font></b> <b><font color="#b91c1c">-2</font></b></small> "]:::added -->|"+ apply"| b["+ Save provider section; roll back a failed apply<br/><br/><small><u>runtime/ngrok_provider.py:209</u><br/><b><font color="#15803d">+37</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 b -->|"+ start"| c["+ Launch agent with inspection disabled; discard raw output<br/><br/><small><u>runtime/ngrok_provider.py:122</u><br/><b><font color="#15803d">+54</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 c -->|"+ record birth identity"| d["+ Use a cross-process lock and verified process identity<br/><br/><small><u>runtime/ngrok_provider.py:2</u><br/><b><font color="#15803d">+106</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 d -->|"+ owned shutdown only"| e["+ Stop only the owned agent; preserve external routes<br/><br/><small><u>runtime/ngrok_provider.py:178</u><br/><b><font color="#15803d">+35</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 l["~ Gateway startup/shutdown reconciles configured providers<br/><br/><small><u>runtime/ingress.py:36</u><br/><b><font color="#15803d">+24</font></b> <b><font color="#b91c1c">-8</font></b></small><br/><small><u>runtime/service.py:681</u><br/><b><font color="#15803d">+1</font></b> <b><font color="#b91c1c">-1</font></b></small> "]:::changed -->|"~ enabled providers"| c
 d -->|"+ liveness observation"| t["+ Keep process state separate from public health<br/><br/><small><u>runtime/topology.py:406</u><br/><b><font color="#15803d">+25</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::added
 ui["+ Provider-specific dashboard configuration<br/><br/><small><u>runtime/static/app.js:948</u><br/><b><font color="#15803d">+20</font></b> <b><font color="#b91c1c">-1</font></b></small> "]:::added -->|"+ explicit apply"| a
 p["Proof: 17 mocked provider tests pass. Existing CLI startup tests pass. No live ngrok account is verified."]:::same
 t --- p
```

## Read-only installation and recoverable onboarding

```mermaid
---
title: Read-only installation and recoverable onboarding — 66c4891 to 564b6fc
---
flowchart TB
classDef added fill:#dcfce7,stroke:#16a34a,color:#14532d
classDef changed fill:#fef9c3,stroke:#ca8a04,color:#713f12
classDef same fill:#f8fafc,stroke:#94a3b8,color:#334155
a["Run installer as the intended service account"]:::same -->|"+ preflight"| b["~ Inventory local infrastructure; validate settings; generate credentials once; show readable progress<br/><br/><small><u>install.sh:324</u><br/><b><font color="#15803d">+1</font></b> <b><font color="#b91c1c">-0</font></b></small><br/><small><u>runtime/cli.py:85</u><br/><b><font color="#15803d">+113</font></b> <b><font color="#b91c1c">-12</font></b></small><br/><small><u>runtime/install_discovery.py:2</u><br/><b><font color="#15803d">+45</font></b> <b><font color="#b91c1c">-0</font></b></small><br/><small><u>runtime/static/app.js:1217</u><br/><b><font color="#15803d">+4</font></b> <b><font color="#b91c1c">-0</font></b></small> "]:::changed
 b -->|"~ explicit exposure selection"| c["Configure local, Serve/Funnel, named Cloudflare or fixed ngrok"]:::changed
 c -->|"unchanged checks"| d["Start gateway, health, MCP, report and recovery instructions"]:::same
 d -->|"+ later account changes"| e["Dashboard account and reusable consent-key settings"]:::added
 p["Proof: 8 installer tests, 8 CLI startup tests and real isolated install-to-shutdown smoke pass. No SSH host is contacted by preflight."]:::same
 d --- p
```

## Scope and limits

Services Edit is verified by six Node tests and the focused field capture. Its small scroll/focus change does not need another chart.

DOTRIX geometry, branding references, and documentation have no additional backend flow. Six current stills cover their visible changes.

Real browser clients, a live ngrok account, and ora8 worker installation remain unverified as recorded in the checkpoint. The newest wordmark export succeeds and receives separate visual proof.
