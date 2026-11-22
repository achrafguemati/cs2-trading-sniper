---
name: research-refresh
description: Executes an on-demand research refresh across GitHub, Reddit, and Steam documentation before starting new milestones.
---

# Research Refresh Workflow Skill

This skill defines the structured pre-milestone research refresh procedure to be executed prior to beginning any new implementation milestone (e.g. before Milestone 2, Milestone 3, etc.).

It ensures that the project's assumptions, schemas, rate-limiting rules, and legal assessments remain aligned with live market conditions and recent open-source developments.

---

## Invocation

Trigger this workflow on demand when preparing to transition between development milestones or upon significant Valve platform updates:

`/research-refresh`

---

## 1. Milestone Gatekeeper Checklist

Execute the following 6-step checklist sequentially:

### Step 1: Open-Source Repository Refresh
- Inspect the active repositories in `docs/research/open-source-project-watchlist.md`.
- Query GitHub for commits, tags, or releases pushed within the last 30–60 days.
- Note any changes in API endpoints, data models, or breaking changes reported by maintainers.
- Audit newly discovered candidate repositories using the `github-open-source-audit` skill.

### Step 2: Reddit Community Intelligence Scan
- Scan `r/csgomarketforum`, `r/GlobalOffensiveTrade`, and `r/cs2` using the `reddit-community-intel` skill.
- Search for recent discussions regarding:
  - Buy order matching priority or ghost order anomalies.
  - Valve fee modifications or regional currency minimum adjustments.
  - HTTP 429 rate-limiting severity or CDN challenge shifts.
  - Secondary marketplace payout policies or KYC requirements.
- Classify all findings using the 5-tier confidence standard.

### Step 3: Steam Official Documentation & Schema Check
- Verify the official Valve Steam Community Market FAQ for text revisions.
- Review Steamworks documentation regarding currency IDs and pricing increments.
- Verify whether the modern SSR React hydration structure (`valve-ssr-data`, `loaderData[3]`) remains stable.

### Step 4: Delta Ingestion & Document Update
- Update ONLY changed or newly discovered items in:
  - `docs/research/open-source-project-watchlist.md`
  - `docs/research/reddit-community-intelligence.md`
  - `docs/research/research-source-register.md`
- Do not rewrite unchanged historical research.

### Step 5: Architectural Impact Assessment
Evaluate the refresh findings against current specifications:
- Does any finding invalidate existing schemas, data models, or rate-limiting delays?
- Does any finding alter the acceptance criteria for the upcoming milestone?
- Does any finding introduce new security or compliance constraints?

### Step 6: Gatekeeper Report
Produce a concise summary for the project owner:
1. Repositories refreshed and new commits/versions inspected.
2. New community intelligence items categorized with confidence labels.
3. Official Steam policy or schema changes detected.
4. Specific impact (if any) on upcoming milestone architecture or roadmap.
5. Recommendation to PROCEED or PAUSE for specification revision.
