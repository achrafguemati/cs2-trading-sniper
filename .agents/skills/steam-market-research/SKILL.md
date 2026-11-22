---
name: steam-market-research
description: Monitors official Valve documentation, Steam Market SSR structures, API schemas, and currency-specific limits.
---

# Steam Market Research Skill

This skill guides the ongoing technical investigation, verification, and schema tracking of the official Steam Community Market (SCM), Valve documentation, and underlying network protocols.

## Core Objective

Maintain an authoritative, empirically verified reference of Steam Community Market mechanics, official fee formulas, SSR React hydration architectures, orderbook structures, and regional currency policies.

---

## 1. Monitored Surfaces

1. **Official Valve Documentation:**
   - Steam Community Market FAQ (fees, limits, restrictions, buy order matching).
   - Steamworks Partner Documentation (currency IDs, price increments, regional pricing).
   - Steam Support articles on Market and Trade Restrictions.
2. **Web & Protocol Architecture:**
   - Modern React SSR structure (`valve-ssr-data` script tags, `loaderData[3]` payloads).
   - Grouped market URL formats (`/market/listings/730/G...`).
   - Wear bucket hierarchies and asset descriptors.
3. **API & Data Endpoints:**
   - `/market/itemordershistogram` (buy order depth, lowest sell ask, cumulative counts).
   - `/market/listings/730/.../render/` (paginated asset and listing details).
   - `/inventory/{steamid64}/730/2` (public CS2 inventory, marketability restrictions, asset IDs).
4. **Financial & Policy Rules:**
   - Exact dual-floor fee schedule (Steam 5% + Publisher 10% with individual floor logic).
   - Currency-specific minimum listing prices and allowed price steps.
   - 7-day marketable hold tracking vs. trade protection restrictions.

---

## 2. Research & Verification Standards

1. **Hierarchy of Truth:**
   - Tier 1: Official Valve documentation and signed platform responses.
   - Tier 2: Deterministic empirical capture reproducible across multiple requests.
   - Tier 3: Third-party reverse-engineering reports (must be independently verified).
2. **Empirical Fixture Preservation:** Any newly observed schema variation or protocol behavior must be normalized, scrubbed of sensitive identifiers, and preserved under `tests/fixtures/` with provenance logged in `tests/fixtures/manifest.json`.
3. **UTC Timestamps:** All observations, network logs, and status records must explicitly state the observation timestamp in ISO 8601 UTC.
4. **Safety Compliance:**
   - Strictly read-only observation.
   - Zero credential collection, browser cookie injection, or authenticated session replay.
   - Respect rate-limiting budgets and global backoff mandates.

---

## 3. Register Maintenance

All verified changes, endpoint observations, and currency configurations must be maintained in `docs/research/research-source-register.md` and referenced across the strategy specifications.
