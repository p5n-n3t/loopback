# Handoff verification and limits

## Scope

The branch delta adds documentation and the two unchanged owner reports.
No Loopback runtime, dashboard, policy, installer or provider implementation changes in this delivery.
`/proof` therefore routes to `/expect-proof` for repository Markdown that no application preview route renders.
The artifact label is **Opus Handoff — Expectations** in the task verification set.
The sheet is an illustrative expectation summary, not a screenshot of GitHub or a provider session.
The handoff PR links the hosted artifact after capture.

## Checked expectations

| # | Expectation | Verdict and source |
| --- | --- | --- |
| 1 | A new account has an entry point and exact repository paths | Met: [README.md](README.md), [COPY-PASTE-PROMPT.md](COPY-PASTE-PROMPT.md). Local Markdown targets resolve. |
| 2 | Both complete reports retain their original bytes | Met: [REPORT-PROVENANCE.json](REPORT-PROVENANCE.json). Byte counts and SHA-256 values match the downloaded files. |
| 3 | Recommendations have priorities, code locations and acceptance gates | Met for the documented plan: [OPUS-HANDOFF.md](OPUS-HANDOFF.md) has H01–H30. Both extraction files retain the source recommendation ledgers. |
| 4 | Discovery covers provider, enrollment and deep inventory cases | Met for specification content: [DISCOVERY-DESIGN.md](DISCOVERY-DESIGN.md) has S0–S9 and D01–D38. |
| 5 | Mesh setup, dashboard details and later lab preparation have separate specifications | Met for specification content: [CLOUDFLARE-MESH.md](CLOUDFLARE-MESH.md), [DASHBOARD-UX.md](DASHBOARD-UX.md), [LAB-ROADMAP.md](LAB-ROADMAP.md). |
| 6 | Reuse research records fit, license and integration cost | Met for research content: [REUSE-RESEARCH.md](REUSE-RESEARCH.md) covers 13 candidates and distinguishes optional runtime dependencies from workflow references. |
| 7 | Existing images remain accessible and distinguish current from historical evidence | Met at this check: [EVIDENCE.json](EVIDENCE.json) has 17 current and 18 historical unique URLs. All 35 return HTTP 200 to HEAD requests. |
| 8 | The launch prompt directs cheap delegation and screenshot reuse | Met: [COPY-PASTE-PROMPT.md](COPY-PASTE-PROMPT.md) and [EVIDENCE.md](EVIDENCE.md). |
| 9 | Proposed product fixes and provider integration work in production | Unverified: this delivery implements documentation only. The next agent must satisfy the release and scenario gates. |
| 10 | Another account can access the repository and sees the exact GitHub Markdown layout | Unverified: account permissions and GitHub rendering require that destination. The isolated sheet does not establish them. |

The primary agent reviewed the package and incorporated independent lower-cost reviews against both complete reports.
These checks establish handoff content and consistency. They do not certify a future implementation or every third-party statement in the reports.

## Reproducible checks

- Resolve each relative Markdown link under `docs/handoff/` and parse both JSON manifests.
- Recompute each report's byte count and SHA-256 from `REPORT-PROVENANCE.json`.
- Assert that the work-package IDs are exactly H01–H30 and the scenario IDs are exactly D01–D38.
- Assert that the evidence manifest has 35 distinct URLs, split 17 current and 18 historical.
- Send bounded HEAD requests to those evidence URLs. Preserve current and historical classifications regardless of HTTP availability.
- Run `git diff --check` for authored handoff files. The unchanged ChatGPT report retains its original Markdown hard-break spaces.
- Compare the branch against the merge-base with `origin/main`. Confirm that no executable source changes.
- Inspect the expectation-sheet render and list the final verification set. Require zero stale items.

Do not run unrelated runtime test suites merely to support a documentation-only delivery.
New production code, provider enrollment and the later VM lab require their own tests and current proof.
