# Sweep: one customer, every internal system

**Proposed by:** Ian (#12) · **Track:** Wordsmith (+ MetaView best overall). Not HRT or G-Research.
**Council:** quick score as written, Wordsmith **84.5** (Sonnet 84 · Opus 85). Second-best Wordsmith idea, just behind Invisible Ink (86.3).

> A bank colleague on a call spends minutes clicking through five old systems to answer one question. Sweep sends a browser agent through all of them at once. It answers in about a minute with every fact cited to the screen it came from, then compiles a full UK GDPR subject-access pack from the same systems.

## The vision
Banks hold plenty of customer data, but it is spread across separate internal systems: core banking, CRM, cards, scanned letters and complaints. Chatbots can't reach those systems, so service staff are trained to click through many screens. Sweep is a browser agent that does that clicking. It plans which systems to visit, opens them in parallel, matches the customer across them by date of birth and postcode (never name alone), and reconciles any conflicts. It then writes the answer with citations. Every page and field it touches goes into an audit log, because this is personal data.

## Who it is for
Contact-centre and branch colleagues answering live queries, and the data-protection team that must answer subject-access requests (UK GDPR Art. 15) within one month.

## What the judges see
| Sponsor | What they get |
|---|---|
| Wordsmith | Live browser tiles, one per system. A workflow graph that branches by system type: terminal-style screen, single-page app, iframes, scanned PDFs. Identity resolution, revisits when systems conflict, and recovery when a session times out. It ends in a legal artefact: a subject-access pack with redactions and citations. Counters show steps, pages, tokens, model, wall time and cost. A cheap model navigates and a strong model writes, with first-run and cached-run cost shown side by side. |
| MetaView | A relatable workplace problem, and a measured stopwatch race between a teammate and the agent. |

## The 90-second demo
1. **0–15s:** "Why was my card declined, and when will my complaint be answered?" A teammate starts clicking through the systems while a stopwatch runs.
2. **15–45s, the race:** Sweep opens four systems as tiles and answers in two lines, each fact linked to its screen. The time difference is **measured**: 20 golden tasks, human vs agent.
3. **45–75s, the sweep:** "Give me everything you hold on me." The agent fans out across every system. It redacts the joint-account holder's details and refuses a scanned letter that contains injected instructions, flagging it. It produces a subject-access pack with a cover letter stating the statutory deadline.
4. **75–90s:** the audit log, the counters, cached vs first run, and accuracy on the golden tasks.

## How it works
```
Colleague query / DSAR request
        ▼
Orchestrator (strong model): which systems, in what order, what to extract
        ▼  parallel browser sub-agents (cheap model, Browser Use / Playwright)
Core banking (Apache Fineract + Mifos UI) · CRM + complaints (EspoCRM Cases)
Cards (mock, legacy iframes) · Document store (mock, scanned letters)
        ▼
Identity resolver (DOB + postcode + account number) → Validator (conflict → revisit; missing → retry once → flag)
        ▼
Writer (strong model): 2-line cited answer | DSAR pack (redacted)
        ▼
Audit log + evidence table (field, value, URL, screenshot, model, tokens, wall_s)
```
The agent is read-only by default, and nothing leaves the system without human approval. When confidence is low it escalates to a human instead of guessing.

### Council build notes (important)
- **Scope (judges split, but agree on the order):** the race is P0 and must work by Saturday night. The DSAR pack is P1, built on the same retrieval engine, and earns the "ideally legally involved" bonus (Opus). Sonnet would keep the DSAR pack as a Sunday-morning stretch goal.
- **Keep the planted prompt-injection refusal:** it is cheap, visible and legal-flavoured (both judges).
- **Hard gate in the first 3 hours:** Fineract and EspoCRM running in Docker with seed data loaded. If Fineract fights back, swap in a lighter open-source core-banking or ERP system rather than mocking it (Opus).
- **Fix the 20 golden tasks and the scoring harness by Saturday afternoon.** The measured race is what turns the demo into a result (Sonnet).
- **Cut first if behind:** make redaction simple rule-based masking, and drop the legacy-iframe cards mock (Opus).

## Data and tools (checked 7 Nov 2026)
- **Apache Fineract** (core banking): Apache-2.0, actively maintained.
- **Mifos web UI:** MPL-2.0. A public demo and sandbox both returned 200 (they reset every 6 h). Docker Compose setup is in its README. Self-host it for the demo.
- **EspoCRM:** AGPL-3.0, with an official Docker repo. Its Cases module serves as the complaints log.
- **Seed data:** about 200 synthetic customers (Faker), with planted edge cases: same-name customers, an address change that didn't reach every system, a joint account, a closed account, a vulnerability flag, and one prompt-injection letter.
- **Background:** WorkArena (ServiceNow Research, ICML 2024) shows that web agents remain far from fully automating enterprise UI tasks.

## 24-hour build plan (3 people)
| Hours | Work |
|---|---|
| 0–3 | Fineract + Mifos and EspoCRM running in Docker; seed script. **Gate** (Ian's Mac has no Docker, so someone else hosts) |
| 0–6 | Agent skeleton on one system; evidence rows with counters; the 20 golden tasks and scoring harness |
| **6** | **GO/NO-GO:** the agent answers 5 golden tasks across 2 real systems, with citations |
| 6–14 | Parallel sub-agents, identity resolver, validator revisits; race end to end (P0) |
| 12–18 | Mission-control UI; session timing teammates on the golden tasks |
| 14–20 | DSAR pack with redaction and the injection refusal (P1) |
| 20–24 | Scores, backup video, rehearse, submit by 11:30 |

## Risks
- **Setup weight:** Fineract is a heavy Java stack, and two mocks plus seed data all come before any agent work.
- **"Real site" is softer** than live public registers, because the environment is self-hosted.
- **Prior art:** vendor copilots exist inside single products. Our angle is a sweep across systems with a legal-grade audit trail and a DSAR output.
- **Synthetic data:** time savings are measured on our setup, not at a real bank.
