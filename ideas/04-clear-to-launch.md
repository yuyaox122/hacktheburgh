# Clear to Launch

**Track:** Wordsmith AI · **Council final mean:** 83.3 (Sonnet 80 · Opus 90 · Grok 80) · **Rank:** 4

> Tell us your startup idea. One agent invents names; the other tries to kill every one on the live trade-mark registers. The survivor comes with a clearance memo and a filled application.

## The vision
Choosing a brand name you can legally use is real legal work: paralegals search several registers that mostly have **no API** (the UK IPO has said so publicly). Clear to Launch turns it into a live duel between two browser agents. The **Founder** proposes names. The **Opposition** hunts for earlier conflicting rights on UK IPO, TMview, Companies House, domains and app stores, and kills each name with a visible legal reason under the Trade Marks Act 1994 s5. One name survives. The output is a clearance memo with screenshots and the UK application filled up to the payment step.

## Who it is for
Founders naming a company, and the in-house and paralegal teams who do clearance searches today, which is Wordsmith's customer base.

## What the judges see
- A complex, real-site, end-to-end task (Wordsmith's stated preference), with a browser that is *needed*, not decorative.
- Head-to-head agent energy (Browser Brawl pattern) with an unscripted input from a judge.
- Token efficiency measured live: our compiled, accessibility-tree agent vs a naive vision agent on the same name.

## The 90-second demo
1. **0–10s**: A judge says "AI tool for sheep farmers" (and may propose a name).
2. **10–60s**: Eight names on a board. Opposition tiles flash across the live registers:
   - *FLOCKLY*: **killed**. UK mark in classes 9 and 42; phonetic 0.92 × class overlap → s5(2)
   - *SHEEPSHAPE*: **killed**. Live Ltd company, .com taken
   - each kill shows its reasoning matrix (phonetic score × edit distance × Nice-class overlap → s5 ground)
3. **60–75s**: One name survives. Clearance memo with screenshots (labelled "screening, not legal advice").
4. **75–90s**: The UK IPO application filled to the payment page (£205 online fee). Token meter: **38k (ours) vs ~400k (vision agent, measured live on one name)**.

## How it works
```
Judge prompt ─► Founder agent (LLM) ─► candidate names
                                        ▼
Opposition agent ── per-register compiled skills (Playwright, accessibility tree):
   UK IPO search (trademarks.ipo.gov.uk) · TMview · Companies House API · RDAP (domains) · iTunes Search API
                                        ▼
Deterministic similarity (zero tokens): Double Metaphone + Levenshtein
LLM only for: Nice-class goods/services similarity, final memo
                                        ▼
Kill board UI + reasoning matrix + token meter (ours vs parallel vision agent)
                                        ▼
Clearance memo (PDF) + UK application filled to payment (or prefilled TM3 if an account is required)
```

## Data and tools
- UK IPO trade-mark search: public web search, no API; **verify unauthenticated automation at hour 0**
- TMview (covers UK marks): fallback primary register
- Companies House API (free key) · RDAP (keyless) · iTunes Search API (keyless)
- Playwright · `jellyfish`/`metaphone` for phonetics · small + large LLM

## 24-hour build plan

| Hours | Work |
|---|---|
| **0–1** | **GO/NO-GO #1**: does IPO search run unauthenticated under Playwright? If not, TMview becomes primary |
| 1–5 | Register skills with golden traces (IPO/TMview, Companies House, RDAP, iTunes) |
| 4–8 | Similarity engine + reasoning matrix + duel loop |
| **8** | **GO/NO-GO #2**: 10 prompts complete end-to-end |
| 8–14 | Memo generator; application fill to payment (or TM3 prefill) |
| 10–18 | Kill-board UI, tiles, token meter, parallel vision-agent baseline |
| 18–22 | **Record an unattended end-to-end run** as the safety net; pre-cache 20 prompts |
| 22–24 | Rehearse, submit |

## Risks
- IPO search blocks automation or changes UI (One IPO migration) → TMview path tested by hour 1.
- Kills look arbitrary if class overlap is loose → the reasoning matrix is always on screen.
- Online filing may need an IP account → prefill the TM3 form instead.
- Sonnet and Grok won't credit it until the unattended run actually exists → record it by hour 22.

## Comparable past winners
- **Browser Brawl (1st, YC × Browser Use):** adversarial agents on live sites. Here the adversary is a legal opposition.
- **Superwizard AI:** real-site browser automation completing a multi-step task.

## Council notes
Opus scored it 90: "every one of my push-over-90 items is in." Sonnet 80 and Grok 80 both hinge on register automation working on the day.
