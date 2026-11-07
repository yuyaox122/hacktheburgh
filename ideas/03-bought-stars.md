# Bought Stars

**Track:** G-Research × HRT · **Council final mean:** 84.3 (Sonnet 90 · Opus 86 · Grok 77) · **Rank:** 3

> GitHub stars are bought by the million, mostly to push malware. On a repo page you can't tell. In account × repo × time space the farms light up, and they tell you which repo will be taken down next.

## The vision
Developers and investors treat stars as trust. Star farms sell them, mostly to promote short-lived malware (pirated software, game cheats, crypto bots). The ICSE 2026 StarScout paper found ~6.0M suspected fake stars across 18,617 repos (July 2019–Dec 2024), detected offline. Bought Stars asks the forward question: **from a repo's first 48 hours of stars, will it be malware that disappears within 30 days?** The campaigns, clusters of throwaway accounts starring the same repos within minutes over and over, are both the 3D reveal and the predictor.

## Who it is for
A developer about to `git clone` a trending repo ("clone / sandbox / avoid"), and an investor who uses stars as a traction signal ("real-star adjusted growth").

## What the judges see

| Sponsor | What they get |
|---|---|
| G-Research | A Git Bang-style galaxy where lockstep star campaigns snap into red constellations: invisible on any single repo, obvious in the right space. |
| HRT | A time-split prediction (AUC vs star-velocity and a StarScout-style baseline at the same 48h cutoff) and a per-repo decision. |

## The 90-second demo
1. **0–20s**: A judge pastes a trending repo. Its stargazers fly in as points.
2. **20–40s**: 80% of them snap into three red clusters. "These same accounts starred 40 other repos. 31 are already gone."
3. **40–55s**: Verdict panel: **P(takedown in 30 days) = 0.83 → SANDBOX IT**, with the features that drove it.
4. **55–75s**: Replay a known case from 2025: "flagged at hour 36", then the repo's disappearance from our hourly polling.
5. **75–90s**: Scoreboard: AUC vs star velocity and vs a StarScout-style detector at 48h, on post-paper data the paper never saw.

## How it works
```
GH Archive (BigQuery / hourly dumps), 2025–2026 WatchEvents
        ▼
lockstep detector: account×repo×time co-starring windows → campaign clusters
        ▼
features at 48h: campaign tightness, farm reuse (other repos hit), account age/emptiness, star velocity
        ▼
label: repo 404/disabled within 30 days (GitHub REST, plus our own hourly polling for exact dates)
        ▼
gradient-boosted classifier, strict time split ─► AUC vs baselines (the gate)
        ▼
precomputed galaxy JSON (20 demo repos) + live lookup with cached token ─► three.js / R3F galaxy
```

## The honest framing (after council objections)
- **Removal dates aren't observable historically** (Opus, Grok). Headline the AUC, not "days before GitHub". Poll candidate repos hourly *during* the hackathon to get exact removal times going forward.
- Deletion is a noisy proxy for malware: it also covers spam and user deletions. Say so, and show a hand-checked sample.
- StarScout is the detection prior art. Ours is *early prediction + a decision + post-paper data*.

## Data and tools
GH Archive (public; BigQuery free sandbox) · GitHub REST API (free token, 5k req/h) for status labels and stargazers · StarScout paper for method and baseline (arXiv 2412.13459) · Python (polars, LightGBM) · three.js / React Three Fiber.

## 24-hour build plan

| Hours | Work |
|---|---|
| 0–4 | BigQuery pull of 2025 WatchEvents to parquet; repo-status label pull in parallel; start hourly polling of fresh candidates |
| 4–8 | Lockstep detector; 48h campaign features; classifier on a strict time split |
| **8** | **GO/NO-GO**: campaign features beat star velocity by ≥0.05 AUC? |
| 8–16 | Galaxy UI, paste-a-repo lookup, cluster highlighting |
| 16–20 | Decision panel (clone/sandbox/avoid, real-star growth); replay scene |
| 20–24 | Precompute 20 demo repos; rehearse; submit |

**Fallback:** reveal + per-repo star-authenticity score, with no takedown claim.

## Risks
Label noise and circularity (above) · GH Archive volume (sample to 6 months, star events only) · API limits during a live paste (precompute the demo set).

## Comparable past winners
- **The Git Bang (grand prize):** a 3D galaxy of GitHub data. Same domain and visual language, with a security payload.
- **ICHack HRT prize:** structure → forecast → decision.

## Council notes
Sonnet raised it to 90 after the reframe. Opus 86 and Grok 77 both flag the removal-date problem; Grok's remaining objection is that "days before GitHub's detector" isn't observable, hence the AUC-first framing above.
