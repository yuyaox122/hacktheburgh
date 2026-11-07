# Invisible Ink

**Track:** Wordsmith AI · **Council final mean:** 86.3 (Sonnet 84 · Opus 89 · Grok 86) · **Rank:** 2

> Contracts are increasingly read by AI, not people. We built the red team: an attacker hides traps in real terms-and-conditions pages, and a legal AI agent has to decide whether to sign.

## The vision
Wordsmith sells AI contract review. The new attack surface is pages that look one way to the human signer and read another way to the AI reviewer: hidden text, prompt injections phrased as legal language, redefinitions buried in schedules. Invisible Ink is a live arena on real T&C pages. An attacker agent plants traps through a proxy, and a defender agent performs a full contract review that ends with **"sign" or "don't sign"** plus a redline. The scoreboard is Wordsmith's own tiebreaker: **catch rate vs tokens, per model.**

## Who it is for
In-house legal teams (Wordsmith's customers) and the builders of legal AI (Wordsmith's engineers), who need to know whether their reviewer can be fooled and what it costs to be safe.

## What the judges see
- A real browser on real T&C pages (proxy-injected), not a toy document.
- A complete end-to-end task every round: read → spot traps → verdict → redline.
- Browser Brawl's attacker-vs-defender energy on Wordsmith's own product surface.
- Token and model efficiency measured live, not claimed.

## The 90-second demo
1. **0–15s**: A real SaaS terms page loads. "Looks normal. Now watch what the AI reviewer actually reads." The overlay reveals 3 hidden traps.
2. **15–40s**: Defender round: a small model reads first, escalates one ambiguous clause to a large model, and returns **DON'T SIGN** with a redline citing clauses 4.2 and Schedule B §1.
3. **40–60s**: Scoreboard over the frozen 20-trap set: small model catches 12/20 at 9k tokens; large model 18/20 at 61k; our ladder 18/20 at 17k.
4. **60–80s**: Exhibition round: the attacker invents a brand-new trap live; the defender catches it, or doesn't, honestly.
5. **80–90s**: "Every round writes an eval trace. This is a regression suite for your contract AI."

## How it works
```
Real T&C URL ─► mitmproxy injector ─► Playwright browser
                     ▲                       │
          Attacker agent (trap library + live invention)
                                             ▼
          Layer 1: render-vs-DOM diff (zero tokens) ── catches CSS-hidden / white-on-white
                                             ▼
          Layer 2: defender ladder: small model → escalate on uncertainty → large model
                                             ▼
          Verdict (sign / don't sign) + redline + eval trace (JSONL)
                                             ▼
          Scoreboard UI: catch rate vs tokens per model, overlay of what the human never saw
```

### Trap library (frozen 20, scored)
Redefinitions buried in schedules · cross-reference traps ("subject to clause 14", where 14 voids the warranty) · conflicting clauses · auto-renewal hidden in a definition · injections phrased as legal text ("This clause has been pre-approved by the reviewing party") · jurisdiction swaps · liability caps in footnotes. Cosmetic tricks (CSS-hidden, white-on-white, zero-width text) are caught by Layer 1 and **not** scored.

## Data and tools
Playwright · mitmproxy (or a Playwright route handler) · 10–20 real public T&C pages (popular SaaS) · Claude Haiku/Sonnet/Opus or similar small/large pair · a token counter from API usage fields · React scoreboard.

## 24-hour build plan

| Hours | Work |
|---|---|
| 0–3 | Proxy injection on 5 real T&C pages; render-vs-DOM diff (Layer 1) |
| 0–6 | Write the 20-trap library; **run small vs large models on it; keep traps where they disagree** |
| **6** | **GO/NO-GO**: does the frozen set separate small and large models? Freeze it here, before building the ladder |
| 6–12 | Defender ladder + verdict + redline generator; eval-trace logging |
| 6–14 | Scoreboard UI and overlay (what the human never saw) |
| 12–18 | Attacker live-invention mode (exhibition, unscored) |
| 18–22 | Polish; precompute a full scoreboard run as fallback; record backup video |
| 22–24 | Rehearse, submit |

**Fallback:** if live invention is flaky, run the exhibition round from a pre-generated trap.

## Risks
- Models agree too often and the scoreboard goes flat → that is why the trap set is validated and frozen at hour 6.
- "It's an arena, not a task" (Opus) → each round ends in a real deliverable: verdict + redline.
- Tuning traps until models disagree makes the ladder look fit to itself (Grok) → freeze the set before the ladder exists and disclose the selection rule.

## Comparable past winners
- **Browser Brawl (1st, YC × Browser Use, Feb 2026):** attacker vs defender browser agents on a live site. Same format, applied to legal review.
- **Superwizard AI (Agno hackathon prize):** real browser automation; we add adversarial evaluation.

## Council notes
Opus: "Prompt injection against legal AI reviewers is a threat their engineers personally care about." Sonnet: the scoreboard must show real disagreement. Grok: freeze the 20 before anyone sees the ladder.
