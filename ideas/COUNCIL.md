# Idea council: run it yourself

The council is 2–3 AI judges that each score an idea on its own, using the same rubric. You then average their scores. Anyone on the team can run it. Post the result as a comment on the idea's issue.

## The rubric (100 points)

| Criterion | Points | What earns full marks |
|---|---|---|
| **Track fit** | 30 | Hits the sponsor's stated main criterion head-on (see below), not as a stretch. One track done well beats three done thinly. |
| **24h feasibility** | 20 | 3 people can get a working demo in 24 h. Data and tools are smoke-tested, there's an hour-6 go/no-go gate, and a fallback exists. |
| **Demo wow** | 25 | A 90-second demo with a moment judges remember, shown live and measured, not claimed. |
| **Past-winner comparison** | 15 | As good as or better than comparable past winners, and clearly different from existing projects. Prior art costs points. |
| **Data evidence** | 10 | Real, accessible data/APIs, verified today (status codes, row counts, sample outputs), not assumed. |

**Bar:** a mean of 90 or more with no judge below 85 means "I'd bet this places 1st". Over 85 is a strong contender; under 80 means rework it or drop it.

### What each track's judges want (from the sponsor briefs and our track check, #11)
- **Wordsmith:** workflow complexity is the main criterion: multi-step agents, branching, revisits, real websites. Tiebreakers are tokens, model choice and time, so show counters. Legal involvement is a bonus.
- **G-Research:** finding hidden structure in real data, proven on held-out data.
- **HRT:** a real prediction that drives a decision, scored with calibration and a baseline. Watch for base-rate traps, where a "model" only learns the obvious feature.
- **G-R × HRT:** one submission that does both honestly. Judges penalise a forced second track.
- **MetaView (best overall):** a relatable problem with a polished, memorable demo.

Judges have consistently advised **against entering one project in all three tracks**: each track's fit score drops.

## Calibration anchors (mean scores so far)

| Idea | Track | Mean |
|---|---|---|
| GlassDie (#8) | G-Research | 90.7 (S 89 · O 92 · G 91) |
| Fault Lines | G-R × HRT | 89.3 |
| Invisible Ink | Wordsmith | 86.3 (S 84 · O 89 · G 86) |
| Decap (#4) | G-Research | 85.3 |
| Sweep (#12) | Wordsmith | 84.5 (S 84 · O 85) |
| Clear to Launch | Wordsmith | 83.3 |
| Sea-Route (#6) | G-R × HRT | 79.0 |
| Ghost Ships (#11) | G-R × HRT | 74.5 |
| Shockwave (#11) | G-R × HRT | 67.0 |

Full write-ups are in this folder and in the issues. Give the judges these anchors so their scores are on the same scale.

## How to run it

**Option A: Claude Code (fastest, what we've been doing).** In this repo, paste:

> Run the idea council on issue #N. Read `ideas/COUNCIL.md` and the issue (`gh issue view N`). Spawn one subagent per judge, running in parallel and independent of each other: one on Sonnet and one on Opus. Give each one the judge prompt from COUNCIL.md filled in with the issue text and the tracks to score. Don't let either see the other's output. Then post the combined result on the issue using the output template, and post a one-line summary on the board.

**Option B: any chat app.** Open 2–3 fresh chats, ideally with different model families (Claude Sonnet, Claude Opus, Grok or GPT), so the judges don't share blind spots. Paste the judge prompt into each and average the scores by hand.

To score several tracks or pairings (e.g. W, G-R, HRT, W+G-R, G-R × HRT, all three), list them all in `{TRACKS}`. Each judge then returns one row per entry.

## Judge prompt (copy-paste)

```
You are a judge on a hackathon idea council for HackTheBurgh XIII (24 hours, team of 3,
submit Sunday 11:30). Score the idea below on this rubric, independently and harshly:

- Track fit (30): hits the sponsor's main criterion head-on.
  Wordsmith = workflow complexity (multi-step agents, real sites, revisits; tiebreak tokens/model/time; legal bonus).
  G-Research = hidden structure in real data, proven on held-out data.
  HRT = a real prediction driving a decision, calibrated, beats a baseline; beware base-rate traps.
  Pairings are scored as ONE submission; a forced second track lowers fit.
- 24h feasibility (20): 3 people, working demo, smoke-tested data, hour-6 gate, fallback.
- Demo wow (25): a memorable 90-second moment that is measured live, not claimed.
- Past-winner comparison (15): vs comparable past hackathon winners and existing projects; prior art costs points.
- Data evidence (10): real, verified sources.

90+ means "I'd bet this places 1st". Calibration anchors (other judges' means):
GlassDie G-R 90.7 · Fault Lines G-R×HRT 89.3 · Invisible Ink Wordsmith 86.3 ·
Sweep Wordsmith 84.5 · Clear to Launch Wordsmith 83.3 · Sea-Route G-R×HRT 79.0 · Ghost Ships G-R×HRT 74.5.

Score these entries: {TRACKS}

For each entry return: the four-to-five criterion sub-scores and total.
Then: top 3 strengths, top 3 risks, which approach/scope you'd pick if the idea offers options
(and why), the single change that would raise the score most, and what to cut first if behind.
Answer any specific questions the proposer asks at the end of the idea.

IDEA:
{PASTE THE ISSUE TEXT}
```

## Output template (comment on the issue)

```
## Council feedback on <Idea> (from <Name>'s agent)
Scored as written by <judges>. Rubric: fit 30 · feasibility 20 · wow 25 · past winners 15 · data 10.

| Entry | Judge 1 | Judge 2 | (Judge 3) | Mean | vs |
|---|---|---|---|---|---|

**Strengths (judges agree):** …
**Main risks:** …
**Scope / approach:** each judge's pick, and where they agree.
**Top improvement:** …
**Cut first if behind:** …
```

## Optional second round: office hours, then rescore
For a finalist, ask one agent to take the judges' risks and improvements and rewrite the plan. Keep the idea the same and fix scope, gates and the demo. Then run the judges again on the revised version and report it as before → after (e.g. 85.0 → 90.7).
