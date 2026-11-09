# Research behind the agent layer: AdventureX gallery + DimOS (decided, and what got built)

Ian's research notes, Sat 7 Nov 2026. Earlier summaries were posted on issue #4 ([AdventureX parallels](https://github.com/yuyaox122/hacktheburgh/issues/4#issuecomment-6711594125), [DimOS borrowings](https://github.com/yuyaox122/hacktheburgh/issues/4#issuecomment-6711803739)). This file records what was **decided** and what is **built** as of 22:15, and keeps the full gallery ranking (Appendix) that was only local until now.

**Source:**
- Ian exported the public gallery.adventure-x.cn project list himself: 577 projects from AdventureX 2025/26, ClawHunt, Edge Future and Geekathon. The site blocks automated access, so nothing was scraped.
- The raw export (`advx-gallery-details-2026-11-07.json`, 4.5 MB) stays on Ian's laptop. It's a bulk copy of another site's content, so it isn't republished here.

## 1. AdventureX ideas Decap adopted

| Borrowed from | Decided version in Decap | Status |
|---|---|---|
| **AgentsHub** (AdvX 2026, 2nd): plan → code → debug → verify with a live dashboard | Live agent timeline: every tool call and claim attempt is a transcript event (tool, args, verdict, cells, code lines, tokens, time), streamed over SSE | ✅ built (`decap stream`, `kind="claim"` events); timeline UI prototype in `decap/agent/prototypes/` |
| **Occam** (AdvX 2026): a backtrace engine that verifies and refutes hypotheses | The Witness refine loop: claim → Yosys refutes with the exact input → refine (max 2) → proven | ✅ built (`decap explain`), on Yuyao's checker `core/claims.py` |
| **Gesellschaft** (AdvX 2026, 2nd): pre-registered, hashed protocols; keeps negative results | Freeze hash of code + prompts + model + split + decompiler commit, posted before the held-out run; honesty counts report every failure by chip | ✅ built (`decap bench --freeze-hash`, `--frozen` guard) |
| **guii-devtools** (AdvX 2024) + **Manifold**: click a UI element → its source | Gate ↔ code line: every tool returns `touched {cells, nets, lines}`, and transcript events carry them | ✅ data built; the 3D/web side is Yuyao's |
| **visocode** (AdvX 2025, best technical expression): animated code explainers | Not adopted (time) | ✗ |
| **TruthLink / KEAL**: verifiable credentials | "Silicon CV" MetaView hook | ✗ not pursued |

## 2. DimOS features in the agent layer

DimOS (dimensionalOS/dimos) lets agents drive robots through typed skills over MCP. Ian has used it hands-on (it drove the Piper arm). Decap borrows its agent architecture for chips:

| DimOS feature | Decap agent layer | Status |
|---|---|---|
| `@skill` methods auto-exposed over **MCP** (docstring = description) | Every core FastAPI route becomes an MCP tool via FastMCP `from_fastapi` (operation_id = name, docstring = description): 17 tools | ✅ |
| Point any coding agent at **AGENTS.md** + CLI | `decap/agent/AGENTS.md`, `skill/decap/SKILL.md`, `claude mcp add decap -- decap mcp` | ✅ |
| CLI lifecycle (`run`, `log`, `mcp call`) | `decap tools / call / explain / claim / formalise / bench / demo / replay / stream / mcp` | ✅ |
| Agent transcript as a **stream** a UI renders | JSONL transcript, SSE `/events`, cells + lines at top level | ✅ |
| `--replay` tier | `decap replay FILE --check` re-runs every call and flags mismatches | ✅ |
| `--simulation` tier / swappable typed modules ("blueprints") | Swappable stages by config: core (stub ↔ real), checker (Yosys `claims.py` ↔ simulation fallback), LLM (API ↔ claude CLI ↔ Haiku). The checker swap is measured: `decap bench --ablate` | ✅ |
| Spatial memory, "object permanence" | Chip memory: proven claims indexed by region fingerprint ("seen this before?") | ✗ not built |
| Teleop, drivers, LCM/Zenoh/DDS transports | Skipped on purpose (overhead for chips) | n/a |

**Line:** *"Dimensional lets agents drive robots through skills. Decap gives agents the skills to read silicon, and a proof stands behind every answer."*

## 3. Dropped
- **LibreLane** (deterministic place-and-route after agentic changes): Ian's call on 7 Nov, about 22:10, too big for the value. This Mac has neither Nix nor Docker, and the only other host is a small shared server.

---

## Appendix: AdventureX gallery, ideas worth adapting for HackTheBurgh (Ian's full ranking, 17:40)

The team chose Decap; this is kept as a record. Scores are Ian's own estimates on the council rubric, not council-scored.

### Headline
- **The pool is mostly the wrong kind.** About 85% of the gallery is consumer companions, hardware, AR/VR, Web3 tokens or learning apps. Ideas that suit our tracks cluster in their trading track (**PandaAI 交易未来**), their consumer-finance track (**度小满 Money Whisperer**) and a few security and data projects.
- **Best new find: a strong, HRT-first idea.** **CarryPilot** already did what HRT judges ask for: two years of real funding-rate data, rules frozen on year 1, an honest held-out test on year 2, and an agent that *refuses* bad trades. Our shortlist has no strong HRT-only entry; Fault Lines is G-R × HRT.
- **Two more worth a council score:** **xEngine** (insurance priced from prediction markets, with a real UK legal question about bets vs insurance) and **TiTO** (making the network traffic of your own devices visible).

### Ranked shortlist

| # | Borrowed from | Our adaptation | Main track | My estimate |
|---|---|---|---|---|
| 1 | **CarryPilot** (+ Assay) | **Carry Gatekeeper**: a trading agent that says no | HRT | **84–88** |
| 2 | **xEngine 差分机** (2nd prize, Money Whisperer) | **Price the Uninsurable** | HRT (+ legal memo) | 78–84 |
| 3 | **TiTO / Ditto** | **Phone Home** | G-Research | 80–85 |
| 4 | **国韵 Global / 星槎 StarRaft OS** | **Export Navigator** | Wordsmith | 78–83 |
| 5 | **見頃 Migoro** | **Spring Clock** | G-R × HRT | 76–82 |
| 6 | **看懂医保 MedPay Rewind** (Sincerity award) | **Cliff Map** | G-R + Bank of Scotland | 75–80 |
| 7 | **店判 ShopValidator** | **High Street Graveyard** | G-R × HRT | 75–82 (labels uncertain) |
| 8 | **Assay** (3rd prize, PandaAI) | **Backtest Lie Detector** (fold into #1) | HRT | 80–85 |

---

#### 1. Carry Gatekeeper (from CarryPilot, plus Assay's audit checks)
**What they built:** a crypto "funding-rate carry" trading agent. It merges funding rates, prices and fees from Binance, Bybit and Hyperliquid, then decides **execute / wait / refuse**. The AI explains its decision; deterministic code does the maths and enforces risk limits.
- **Their evidence:** 65,697 funding events (BTC/ETH/SOL, 24 Jul 2024 → 24 Jul 2026). Thresholds were set on year 1 and frozen for year 2.
- **Their held-out result:**
  - Same-venue spot vs perpetual: 12 of 14 thirty-day windows were positive after costs.
  - Cross-venue perpetual vs perpetual: only **8 of 26** were positive. So the right action was usually to refuse.
- They got no award. As a strategy their approach is strong; as an entry, the story was thin.

**Our adaptation:** keep the core and add what HRT judges look for.
1. Predict **P(net carry > 0 over the next N days)** with a model, not just rules, and show its **calibration** (reliability plot, Brier score).
2. Put real costs in: fees, slippage from public order-book depth, and basis drift.
3. Beat two baselines: "chase the highest APR" and "always hold". Make the decision rule cost-aware (expected value vs threshold).
4. Use Assay's five audits as the agent's self-checks: parameter robustness, look-ahead data, cost stress, regime dependence and signal decay.

| | |
|---|---|
| **Data (tested today)** | ✅ Binance, ✅ Bybit and ✅ Hyperliquid funding-rate histories, all keyless. Order-book endpoints are public on all three |
| **Agent workflow** | Natural-language mandate ("BTC/ETH, max 3× leverage, 30 days") → structured task → parallel venue data pulls → deterministic cost and risk engine → model probability → execute / wait / refuse, with a receipt |
| **Display** | A live board of "advertised APR vs true net carry", showing the **APR illusion** collapse once costs come off. A refusal log with reasons. Held-out calibration plot. A judge-picks-a-pair replay |
| **Why it fits HRT** | Their slides literally say "signal creation… statistical models → automated trading decisions" |
| **Risks** | Crypto may feel narrow, so show the method generalises. Funding regimes shift (show performance by regime). Prior art: CarryPilot and many arbitrage bots, so our edge must be rigour and calibration. **Paper trading only**: no live orders |
| **Improves on the original by** | Adding calibration, baselines, cost realism and a pre-registered held-out test |

#### 2. Price the Uninsurable (from xEngine 差分机)
**What they built:** "turn a worry into a policy that pays out automatically". The insight is that insurance and a bet have the same maths. Six sub-agents (global scan, quant data, news hunter, web prober, market scout, intelligence officer) research the risk, then match it to **Polymarket** markets. The premium is computed from the market odds.

**Our adaptation (HRT primary):**
- **Measure how well-calibrated prediction-market prices are** on thousands of *resolved* Polymarket markets. Known effects include the favourite–longshot bias.
- **Decision:** the loading (margin) to charge per probability band so the cover is fairly priced.
- **Wordsmith-style feature:** an agent writes a short memo on whether the product is *insurance or a wager* under UK law. Questions: insurable interest, the FCA perimeter, the Gambling Act 2005. That's real legal content, but keep it as a feature, not a second track entry.

| | |
|---|---|
| **Data (tested today)** | ✅ Polymarket public API, including closed markets with final outcome prices. Pair it with UK-native risks such as CAA flight punctuality statistics (not yet checked) |
| **Display** | A "price a worry" box: type "my Eurostar gets cancelled by a strike", see matched markets, the calibrated probability, the premium and the legal classification |
| **Risks** | Polymarket isn't available to UK users for trading; we only *read* data, and we must say so. Insurance is regulated, so present it as a pricing study and never sell anything. The council warns against spreading over tracks, so lead with HRT |

#### 3. Phone Home (from TiTO)
**What they built:** a home network sentinel. It blocks devices from infecting each other, learns each device's normal behaviour to spot new attacks, logs tamper-proof evidence, and has a voice agent ("your camera just started sending data to a stranger").

**Our adaptation (G-Research):**
- Make the invisible visible: where *our own* devices send data overnight.
- Prove the anomaly detector on **held-out labelled attacks** from public datasets.
- Optional HRT half: a calibrated block / allow / ask decision.

| | |
|---|---|
| **Data (tested today)** | ✅ The CIC IoT 2023 and UNSW IoT traffic dataset pages are both up. Our own capture comes from our own laptops and phones only, **never the venue Wi-Fi** |
| **Display** | A globe of night-time arcs per device, each device's "personality fingerprint", and a replayed attack lighting up |
| **Risks** | Prior art: Princeton IoT Inspector, Little Snitch. The datasets are large, so sample them. Capture permissions on macOS |

#### 4. Export Navigator (from 国韵 Global / 星槎 StarRaft OS)
**What they built:**
- **国韵 Global:** helps Chinese heritage brands go overseas. It scores markets on six dimensions, including compliance difficulty, then decodes the brand for the culture and generates content.
- **StarRaft:** 14 agents across a 10-step chain that includes compliance review.

**Our adaptation (Wordsmith, legal):** a UK small business wants to sell product X into the EU and US. Browser agents work through four sources:
- the UK Trade Tariff;
- EU **Access2Markets** (tariffs and rules of origin under the UK–EU trade agreement);
- the US tariff schedule (HTS);
- product and labelling rules.

They reconcile commodity codes across those sources and produce a **cited compliance memo plus a landed-cost estimate**.

| | |
|---|---|
| **Data (tested today)** | ✅ EU Access2Markets returns 200. ⚠️ The UK Trade Tariff API returned 404 for the code I tried; the format probably needs fixing |
| **Why Wordsmith** | Multi-site, multi-jurisdiction, revisits on code mismatches, a legal artefact at the end |
| **Risks** | It competes with Sweep (84.5) and Invisible Ink (86.3) for the same track. Tariff classification is genuinely hard, so scope it to 3 products |

#### 5. Spring Clock (from 見頃 Migoro)
**What they built:** a live flower-viewing community. Reports carry verifiable capture times, locations down to a path segment, and a **30-day time dial**. AI suggests the species; humans confirm.

**Our adaptation (G-R × HRT):**
- **G-Research:** the hidden climate signal in *when* Britain blooms, from citizen-science records.
- **HRT:** forecast this year's peak bloom from temperature (accumulated warmth, or "degree days"), tested on held-out years. Decision: when to schedule events, or pollen alerts.

| | |
|---|---|
| **Data (tested today)** | ✅ iNaturalist: **1,019** UK observations of flowering cherries (*Prunus*) in 2025, tagged with flowering stage. Open-Meteo history for temperature |
| **Risks** | It's November: no live blossoms, so the demo is historical. Citizen-science effort varies by year and must be normalised. Prior art: George Mason University's cherry-blossom prediction competition, Japan's centuries-long bloom records, and the Woodland Trust's Nature's Calendar. Cite them and compare against them |

#### 6. Cliff Map (from 看懂医保 MedPay Rewind)
**What they built:** "80% reimbursed doesn't mean you pay 20%". It traces a medical bill backwards through each rule layer, with evidence drawers and "confirmed / still to check" states.

**Our adaptation (G-Research + Bank of Scotland):** "why a £1,000 raise can leave you worse off".
- Show **effective marginal tax rates by income** for *your* household: the 60% personal-allowance taper, the child benefit charge, Scottish bands, student-loan repayments, and the Universal Credit taper.
- Trace take-home pay back to each statutory rule, with citations.
- Decision: pension salary sacrifice to escape a cliff.

| | |
|---|---|
| **Data** | ✅ PolicyEngine API (verified earlier today, including Scotland and custom reforms) |
| **Risks** | Prior art: PolicyEngine shows marginal-rate charts, and the IFS publishes this. It reuses the Statute to Street engine, so present it as that idea's Bank of Scotland spin-off |

#### 7. High Street Graveyard (from 店判 ShopValidator)
Covered in the earlier note. ✅ The Food Standards Agency hygiene-ratings API works without a key. ⚠️ Survival labels are the open question: the Companies House REST API needs a free key, and its free bulk file likely lists live companies only.

#### 8. Backtest Lie Detector (from Assay)
Five parallel credibility checks give a KEEP / WATCH / QUARANTINE / RETIRE verdict. **Fold it into #1** as the agent's self-audit, rather than building it as a separate entry.

---

### Add-ons for ideas already on our board
- **Fault Lines + QuakeFeel:** overlay USGS "Did You Feel It?" reports as observed shaking. ✅ **39,888 reports** for Ridgecrest M7.1, with max community intensity 8.7.
- **Sweep + Crisis-Mosaic:** borrow its conflict handling. Sort evidence into *support / conflict / can't tell / stale*, and separate **evidence quality** from **conclusion confidence**. When evidence is missing, send a *targeted verification* question instead of guessing.
- **Any HRT entry + ChemGuard:** its confidence gate auto-acts only above 90%, routes 60–90% to a human, and escalates below 60%. It also tightens the auto threshold to **99% when probabilities are uncalibrated**. That's a quotable rule for the judges.
- **Any entry + Gesellschaft:** pre-register each experiment, hash the protocol, keep negative results, and validate out of sample. That's a strong honesty slide.
- **Agent spending (Sweep / any agent) + PactLedger:** the agent only *proposes* payments; policy checks budget, purpose and whitelist before a human approves. Idempotent receipts.

### Considered and rejected (why)
- **Consumer companions, pets, rings, glasses, sleep apps:** no data-to-decision core. That covers the bulk of 蓝盒子, 弦指 and viaim entries, e.g. Choose Life and Ember.
- **Robots and embodied hardware:** we have no hardware here (RootScope, Blade-Runner, NightWatch, 365 Desk Cleaner).
- **Finance coaches** (AlphaBit Coach, Financial-GodFather, Money Whisperer, Money Prism, CoinCrush, FinSight): a crowded genre. FinSight's fund look-through is already sold commercially (Morningstar X-Ray).
- **Web3 agent economies and tokenisation** (PolyAgentMarket, TruthLink, Makebook, Arena 402): wrong tracks. TruthLink's background checks also profile individuals, a privacy risk.
- **Counter-Strike market arbitrage:** a fun alternative market for HRT. But Steam price history needs a login, and the write-up is thin. Honourable mention.
- **相地 (3D site selection):** a possible "where should I rent" idea for Bank of Scotland, but it's data-heavy, and the "traditional layout" scoring would be hard to justify.
- **ChemGuard on the Tennessee Eastman fault benchmark:** an honest HRT shape, but that benchmark is studied to death. Borrow its confidence gate instead.
- **Learning and knowledge tools** (b-mind-ai, Tunta, PaperPlay, Recap), **games and AR** (FORMOCRACY, NeuroTrip), **social simulations** (Gesellschaft as a product, the 智慧体 decision simulation): weak fit with HRT and G-R data judging.

### Data checked today (7 Nov)
| Source | Status |
|---|---|
| Binance / Bybit / Hyperliquid funding history | ✅ keyless |
| Binance klines + bulk history | ✅ keyless |
| Polymarket public API (closed markets) | ✅ keyless |
| iNaturalist (UK flowering *Prunus* 2025) | ✅ 1,019 observations |
| USGS ComCat "Did You Feel It?" | ✅ |
| Food Standards Agency hygiene ratings | ✅ keyless |
| CIC IoT 2023 / UNSW IoT traces pages | ✅ |
| EU Access2Markets | ✅ |
| PolicyEngine API (UK/US, reforms) | ✅ (earlier today) |
| Companies House REST | ⚠️ free key needed |
| UK Trade Tariff API | ⚠️ 404 on my test (format to check) |
| Stooq / FRED | ❌ bot-walled / timed out |
