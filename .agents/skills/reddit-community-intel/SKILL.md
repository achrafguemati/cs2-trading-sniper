---
name: reddit-community-intel
description: Monitors CS2 trader communities on Reddit to extract market anomalies, buy-order trends, and restriction updates.
---

# Reddit Community Intel Skill

This skill governs the systematic collection, verification, and classification of market intelligence from public Counter-Strike 2 trading and market communities on Reddit.

## Core Objective

Capture early signals regarding Steam Community Market policy updates, unannounced fee or minimum price adjustments, buy-order anomalies, market manipulation trends, and secondary marketplace payout changes, while rigorously filtering out trader speculation and unsubstantiated rumors.

---

## 1. Monitored Communities

- **r/csgomarketforum:** Primary hub for market mechanics, Valve market patches, investment sentiment, buy order trends, and trade hold discussions.
- **r/GlobalOffensiveTrade:** Secondary market trading, sticker/float premiums, and platform payout changes.
- **r/cs2:** General game updates, economy patches, and broad player base reactions.

---

## 2. Research Guidelines & Ethics

1. **Recency Windows:** Prioritize discussions from the last 30 days. Expand to 90 days when investigating macro market trends. Include older discussions only when they document enduring technical mechanics.
2. **Strict Fact Separation:** Separate first-hand user reports with screenshots/logs from trader opinions, market panic, and speculation.
3. **No Sole-Source Strategy:** Never modify application logic or strategy parameters based on a single Reddit post.
4. **Contradiction Checking:** Explicitly check for dissenting comments, alternative explanations, or subsequent debunking within the discussion.
5. **Ethical Access:** Use standard web search or read-only public endpoints without aggressive scraping or circumventing access controls. Summarize insights; never replicate full discussion transcripts.

---

## 3. Confidence Classification Standard

Every captured intelligence item must be assigned one of the following five confidence tiers:

1. **CONFIRMED BY PRIMARY SOURCE:** Supported by official Valve documentation, verified patch notes, or empirically reproduced live market tests.
2. **MULTIPLE COMMUNITY REPORTS:** Independently reported by multiple distinct users across different threads with consistent observations and supporting evidence (e.g. screenshots, transaction IDs).
3. **SINGLE USER REPORT:** An isolated user claim lacking independent corroboration. Flagged for canary monitoring.
4. **SPECULATION:** Unverified hypotheses, predictions, or theories regarding Valve's intentions or market trajectory.
5. **CONTRADICTED / UNRESOLVED:** Disputed claims where community evidence is contradictory or subsequent testing failed to replicate the reported behavior.

---

## 4. Intel Ingestion Workflow

1. **Query & Filter:** Target discussions containing keywords such as `buy order`, `minimum price`, `market update`, `trade hold`, `429`, `cross-market`, `fee adjustment`.
2. **Record Metadata:** Note thread URL, subreddit, post date (UTC), upvotes, comment count, and the specific claim.
3. **Analyze Impact:** Assess whether the finding impacts BUY Sniper, SELL Sniper, Financial Engine, or Rate Limiting.
4. **Log Deliverable:** Append verified intelligence into `docs/research/reddit-community-intelligence.md`.
