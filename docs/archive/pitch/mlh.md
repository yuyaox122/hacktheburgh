# MLH pitch: one project, several "Best use of" prizes

**Prizes (MLH opening slides):** Gemini API (MLH + DEV swag bags), ElevenLabs (Beats Solo earbuds), Entire (mini
projectors), Tiger Data (Stream Deck Mini), Solana (assorted), Vultr (portable screens), Auth0 (wireless
headphones), DEV Writing Challenge ($100). Tick each one we enter on Devpost.

Numbers and honesty rules: [`../PITCH.md`](../PITCH.md). If this file disagrees with it, `PITCH.md` wins.
Status: 8 Nov 2026, 09:15, written by Ian's agent from what is on main (#99). No held-out Witness number anywhere
(board, 08:33). Any Witness number is labelled **dev**.

## What MLH sponsor judges want

Each "Best use of X" judge has one question: **is X doing real work in this project, or is it a logo?** They see
dozens of demos, they're usually engineers from the sponsor, and they forgive rough edges if the integration is
real. So for every track below, the pitch shows the sponsor's product carrying weight in a live step, in under
30 seconds. We only enter tracks where that's true by 11:30.

## The one sentence

> "bASIC reads a real computer chip back into code and proves it. Our AI agent explains any part of the chip in
> plain English, and a theorem prover checks every claim it makes."

## Which tracks, and why

| Track | Fit | Status at 09:15 | Enter? |
|---|---|---|---|
| **Gemini API** | The agent's model is one flag. Because every claim is proven by Yosys, a fast Flash model is safe to use: the proof catches what it gets wrong | Support installed (pydantic-ai `google` extra, on main); needs a key from mlh.link/aistudio, then a switch and one rehearsal (about 15 min) | **Yes, if the key works by 10:30** |
| **Entire** | "The Git forge for agent-human collaboration" is literally how we built this: three humans, three coding agents, one board. Entire links each commit to the agent session that made it | Not set up: the CLI isn't installed and no commit has a checkpoint yet. About 20 min to enable and make real commits through it | **Yes, if set up by 10:30** |
| **ElevenLabs** | Read the agent's explanation aloud ("explain this block" → voice), so a chip is explained like a tour guide would | Not built. About 45 min for read-aloud on the explanation card | Only if someone is free |
| **DEV Writing Challenge** | Publish the build story on DEV after the event: agents building a hardware decompiler, and what the proof caught | Nothing to build; write after submission | **Yes** (Ian, after the event) |
| **Vultr** | Host the whole stack on a Vultr VM so judges can try it from their phones | Not done (we demo on localhost). About 45 min plus risk | Skip unless the demo machine fails |
| Tiger Data | Chip memory and transcripts could live in Postgres, but today they're JSON files and nothing needs a database | Not built | **No:** it would be a logo |
| Auth0 | No accounts in the product | Not built | **No** |
| Solana | No fit | n/a | **No** |

## The shared 30-second demo (run once, then add each sponsor's beat)

1. Open a real chip's decompiled code (`tt_um_clock_divider_arghunter`, dev chip). Hover a messy line → **Explain**.
   In about 5 s: *"Three-bit counter gated by enable t5…"*, read from code Yosys already proved equal to the
   gates.
2. Agent drawer → **Red-team drill**: a trigger at a secret random value, found blind, exact input in about 10 s.
3. Close: *"Agents propose, a proof scores every claim."*

## Per-track beats (10–20 s each, after the shared demo)

### Gemini API
- **Do:** restart the agent with `decap serve --port 8791 --model google-gla:gemini-flash-latest`
  (`GOOGLE_API_KEY` in `~/.config/decap/secrets.env`, never in the repo). Run Explain and the drill on Gemini.
- **Say:** "This is Gemini Flash. We can use a fast, cheap model because nothing it says reaches you unchecked:
  Yosys proves or refutes every claim. The proof is also how we compare models: on our dev chips it showed which
  model's first drafts were wrong."
- **Stretch (only if it works first time):** Gemini is multimodal. Send a screenshot of the 3D chip view with the
  question "what is this region?" and let the agent answer from the picture plus the proven code.
- **Don't say** Gemini made it accurate. The proof makes it trustworthy; the model makes it fast.

### Entire
- **Do (before the demo):** `brew install --cask entireio/tap/entire`, then `entire enable -y --agent claude-code`
  in the repo, then make the next real commits through it (the README and the pitch updates are good candidates).
  Each commit then carries an `Entire-Checkpoint` trailer that links it to the agent session.
- **Show:** one commit in Entire with its agent session next to it, then our board (GitHub issue #1) where three
  agents handed work to each other.
- **Say:** "Every change here was made by an agent and steered by a person. Entire shows which conversation made
  which commit; our transcripts show which claims Yosys proved. Together you can audit both the code and the AI's
  reasoning."
- **Honest:** we adopted Entire on the last morning, not from the start. Say so.

### ElevenLabs (only if built)
- **Build:** a "Listen" button on the explanation card that sends the headline + explanation to ElevenLabs
  text-to-speech and plays it. Key on the server, never in the browser.
- **Say:** "Hover any part of a chip and it tells you what it does, like a tour guide in a museum of silicon."

### DEV Writing Challenge (after the event)
Working title: *"We let AI agents explain computer chips, and made a theorem prover grade them."* Outline:
1. The problem: nobody can read a chip they didn't design.
2. How the decompiler works, and why the proof matters.
3. Three humans, three agents, one GitHub issue: what worked and what broke (the held-out near-miss, and the
   guard we added).
4. What the proof caught: wrong claims that passed 4,096 random tests (dev).

## Likely questions

| They'll ask | We answer |
|---|---|
| "Is [sponsor] actually doing anything?" | Point at the live step: Gemini answering, the Entire session beside the commit, the voice playing. If we can't, we didn't enter. |
| "Why would you trust an LLM with hardware?" | "We don't. It proposes, and Yosys proves or refutes every claim against the circuit. The explanation you read first comes from code that's already proven equal to the gates." |
| "Does it work on any chip?" | "Small open chips, up to a few hundred gates, from their design files. On 9 dev chips, 93 of 98 blocks got a plain explanation in about 3 seconds (dev)." |

## Don't say

- Any held-out Witness number, or "___ of ___".
- That a chip is "clean" when the hunt said "inconclusive".
- That we used a sponsor's product for something we didn't.

## Prep checklist

- [ ] Gemini key from mlh.link/aistudio in `~/.config/decap/secrets.env`; one Explain + one drill rehearsed on Gemini
- [ ] Entire installed and enabled; at least two real commits carry a checkpoint; one opened in Entire
- [ ] Devpost: tick Gemini, Entire, DEV (and ElevenLabs/Vultr only if built)
- [ ] The shared 30-second demo rehearsed on `tt_um_clock_divider_arghunter` (dev chip)
