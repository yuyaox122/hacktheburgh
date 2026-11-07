# Who Knew?

**Track:** G-Research × HRT · **Council final mean:** 82.7 (Sonnet 80 · Opus 87 · Grok 81) · **Rank:** 5

> Some Polymarket wallets keep taking the winning side hours before the news breaks. We find them, show them, and test, honestly, whether following them beats the market.

## The vision
Information leakage is the core microstructure question at trading firms. Polymarket publishes every trade by wallet. Who Knew? scores each wallet's **timing edge**: how often and how early it took the winning side, using only what was known when each market opened. It also finds **lockstep clusters**, wallets that repeatedly enter the same markets within minutes. The constellation of wallets is the reveal. "Market price + informed flow" vs "market price alone" (Brier score, out of sample) is the prediction test, shown **win or lose**.

## Who it is for
A market-integrity analyst asking "where is informed money piling in right now?", and a trader asking whether that signal is worth following.

## What the judges see

| Sponsor | What they get |
|---|---|
| G-Research | A 3D constellation of wallets; clusters that light up hours before news; replay of a famous market moving early. |
| HRT | A strict date-holdout Brier test vs the market price, plus a follow-the-informed-money backtest. Honest if it loses. |

## The 90-second demo
1. **0–20s**: Constellation of 20k wallets. "These are everyone who traded 400 resolved markets."
2. **20–45s**: Replay a famous market: hours before the announcement, one cluster lights up and buys the winning side. The price only moves later.
3. **45–65s**: Brier score: market alone vs market + informed flow, on markets after the training cutoff. Shown whatever the result.
4. **65–80s**: "Leak radar": currently open markets where high-informedness wallets are entering now.
5. **80–90s**: Backtest of the follow strategy vs the market.

## How it works
```
Polymarket Data API v2 /trades (public, no key)  +  Gamma API (resolved markets)
        ▼   (300–500 high-volume resolved markets, pulled Saturday afternoon)
per-wallet timing-edge score, frozen at each market's open date (no leakage)
        ▼
lockstep clustering: wallets co-entering the same markets within minutes, repeatedly
        ▼
model: market price + informed-flow features ─► Brier vs price alone (date holdout) = the gate
        ▼
precomputed JSON ─► three.js constellation + replay + leak radar
```

**Dropped after council review:** the funding-graph merge. It glued strangers together at exchange and bridge deposits, and the Goldsky subgraph it needed is deprecated after Polymarket's V2 migration.

## Data and tools
Polymarket Data API v2 (`/v2/trades`, public) · Gamma API (market metadata and resolutions) · Python (polars, scikit-learn) · three.js.

## 24-hour build plan

| Hours | Work |
|---|---|
| 0–5 | Pull trades for 300–500 resolved markets (paginate, cache to parquet) |
| 3–8 | Timing-edge scores frozen at market open; lockstep clusters; Brier test on a date holdout |
| **8** | **GO/NO-GO**: does informed flow improve Brier out of sample? |
| 8–16 | Constellation UI + famous-market replay |
| 14–20 | Leak radar on open markets; follow-strategy backtest |
| 20–24 | Polish, rehearse, submit |

**Fallback:** reveal + informedness leaderboard, with no beat-the-market claim.

## Risks
- Lockstep clusters may catch copy-traders and market makers, not insiders (Opus, Grok) → label clusters by behaviour; don't call anyone an insider.
- Beating the market price is hard; it may only work in thin markets → show the result either way.
- Pagination limits (~10.5k trades per query) → restrict to high-volume markets and cache.
- Naming wallets as "insiders" is reputationally risky → anonymise and say "informed flow".

## Comparable past winners
- **ICHack HRT prize:** prediction tested against a baseline. Weaker comparable than the top 4.
- **The Git Bang:** 3D constellation of a large public dataset.

## Council notes
Opus 87: "a top-tier reveal for quant judges" if the holdout gain appears. Sonnet 80 and Grok 81: the beat-the-market claim remains the hard part.
