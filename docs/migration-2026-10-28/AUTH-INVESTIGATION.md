# Preliminary OAuth and tool-settings investigation — 2026-10-28

Scope: read-only local code diagnosis for the cross-account handoff. No fixes, live host changes, deployments, browser installations, full-suite runs, or repository edits. Used isolated gateway TestClient state via tests/helpers.py and the existing /tmp/loopback-dashboard-venv. Working tree remains clean at completion of this investigation.

## Owner-reported client failures

The owner reports ChatGPT registration fails with `only public clients (token_endpoint_auth_method=none) are supported`. Claude reports `unsupported grant type requested`. These are owner observations, not fresh real-client reproductions. Exact real registration bodies, endpoint host, and client versions are unavailable. Do not claim a particular real-client authentication method or grant type from the error alone. Do not attribute the errors to merged PR8 without a before/after comparison.

### Confirmed rejection paths

- runtime/auth.py:442–479, AuthStore.register_client: validates redirects, then rejects every token_endpoint_auth_method except `none` at lines 449–451. Omission defaults to `none`.
- runtime/auth.py:452–454 rejects any grant_types element outside `authorization_code` and `refresh_token`. Omission or an empty array defaults to both. There is no metadata-array shape validation here, so malformed truthy values can also reach this rejection.
- runtime/auth.py:460–466 always returns/stores public authentication and both grants, even when the client requests a supported narrower grant list.
- runtime/auth_http.py:258–273 exposes POST /oauth/register and returns these AuthError strings as OAuth error JSON. The client registration failures occur before user consent or token exchange.
- runtime/auth_http.py:188–200 advertises only `none`, `authorization_code`, `refresh_token`, response type code, and PKCE S256.
- runtime/auth_http.py:366–431 token handler reads client_id from form fields. It does not authenticate a client secret or parse HTTP Basic credentials. It requires PKCE for authorization codes and supports only authorization_code and refresh_token. Merely permitting secret methods during registration would leave the token endpoint incompatible and would misrepresent client authentication.
- runtime/auth_http.py:245 resolves CIMD documents through the same register_client validation. Switching to metadata documents does not itself add confidential client support.

### Isolated local reproductions

Each request used POST /oauth/register with redirect_uris `["https://example.com/oauth/callback"]` and a synthetic metadata variation. No provider was contacted.

| Variation | Observed HTTP result |
| --- | --- |
| token_endpoint_auth_method=none | 201, returned none and authorization_code/refresh_token |
| token_endpoint_auth_method=client_secret_basic | 400 invalid_client_metadata, exact owner-reported public-client error |
| token_endpoint_auth_method=client_secret_post | 400 invalid_client_metadata, same error |
| grant_types=[authorization_code,refresh_token,client_credentials] | 400 invalid_client_metadata, exact owner-reported unsupported-grant error |
| grant_types=[authorization_code,implicit] | 400 invalid_client_metadata, same unsupported-grant error |

Inference: the messages are explained by existing metadata validation. The synthetic requests prove rejection conditions, not actual ChatGPT/Claude payloads. The Claude message may also arise from malformed grant_types metadata. The ChatGPT message only establishes a non-`none` value reached this branch, assuming this exact build and endpoint produced it.

## Standards evidence and safe remediation requirements

Primary sources checked on 2026-10-28:

- RFC 7591 section 2: https://www.rfc-editor.org/rfc/rfc7591.html#section-2 . It defines none, client_secret_post, and client_secret_basic. The standard default for omitted token_endpoint_auth_method is client_secret_basic. The standard omitted grant_types default is authorization_code. Loopback's defaults differ.
- RFC 7591 section 3.2.1: https://www.rfc-editor.org/rfc/rfc7591.html#section-3.2.1 . A registration server can reject or substitute requested metadata. A successful subset response still requires client compatibility testing.
- Versioned MCP authorization specification: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization . It describes public/confidential client security and optional DCR. Do not interpret DCR as permission to enable unsupported grants.

Next agent should first obtain redacted real registration metadata for each client. Record the method value, grant and response arrays, and relevant discovery response. Keep Authorization headers, secrets, cookies, codes, tokens and enrollment fragments out of handoff and screenshots. Distinguish controller and independent-worker endpoint hosts and installed versions.

If confidential clients are required, plan the complete change: registration metadata, secret generation and safe storage/migration, one-time secret disclosure, method-bound token authentication, constant-time verification, secret expiry/revocation policy, discovery accuracy, and compatibility with existing public registrations. Authentication must occur before authorization-code consumption or refresh rotation. Preserve PKCE, consent, exact redirect matching, scope limits, audience/resource binding, code single-use and refresh-reuse detection. No blanket acceptance of client_secret_* metadata, no ignored secrets, no machine-token fallback.

If a client asks for extra grants, decide whether an explicitly returned supported subset works for that client. Do not add implicit, password or client_credentials grants as a compatibility shortcut. A client_credentials implementation needs its own reviewed authorization model and cannot inherit an owner's interactive tool access silently.

Required focused tests: real redacted payload fixtures; metadata type validation; supported/unsupported method and grant combinations; defaults and returned metadata; wrong/missing secret; Basic/post method mismatch; public-client preservation; PKCE/redirect/resource/code replay/refresh tests; controller and worker paths; actual external-client consent/refresh/reconnect checks. Existing relevant tests are tests/test_auth_flows.py (registration helper 31–34 omits method/grants, PKCE 102, redirect 120, resource 127, refresh 160/174). Existing local tests do not establish real-client compatibility.

## Tool-settings gap and additional confirmed state bug

- runtime/static/app.js:654–681 contains the tool view, filter and individual checkbox handler. It has no select-all/deselect-all or group-level controls. This confirms the reported bulk-toggle gap.
- More seriously, runtime/static/app.js:680 assigns the complete PUT response to tl.policy. But runtime/admin_api.py:225–242 returns `{policy, warnings, permissions}`, while GET /api/admin/policy returns the policy directly (lines 222–223). After the first toggle, cached allowed_tools/denied_tools disappear from the top level.
- Confirmed with the actual shipped checkbox function executed in Node vm and a mock returning the observed API envelope: disabling tool_one then tool_two produces writes `{"allowed_tools":["*"],"denied_tools":["tool_one"]}` then `{"allowed_tools":[],"denied_tools":["tool_two"]}`. The second write loses the first denial. The API envelope was separately verified with an isolated authenticated TestClient PUT.
- runtime/policy.py:392–401 treats denied_tools containing `*` as deny-all. An empty allowed_tools array falls back to `['*']` at line 399. Thus the lost denial can re-enable the earlier tool after refresh even though its checkbox still appears disabled. Treat this as a policy/UI correctness issue, not only missing convenience controls.

Remediation requirements: normalize the PUT response to its policy payload before another change; serialize rapid changes or use a server revision/merge strategy to prevent lost updates. Then add clearly scoped bulk controls (all tools versus visible filtered tools versus category), preserve unrelated policy fields, and display/persist authoritative results. Decide how wildcard denials interact with enabling a single tool. Never implement deselect-all as allowed_tools=[] under current semantics. Test sequential disables, disable then enable, rapid requests, failure rollback, filtered bulk actions, deny-all and reload persistence. Verify tools/list visibility and tools/call enforcement after changes, not only checkbox appearance. No fix was made in this investigation.
