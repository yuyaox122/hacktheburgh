# JetBrains pitch: "Help the Developer"

**Prize:** 1st €350 per person; 2nd €150 per person. They will ask how we used AI.

Numbers and honesty rules: [`../PITCH.md`](../PITCH.md). If this file disagrees with it, `PITCH.md` wins.
Status: 8 Nov 2026, 00:40.

## What the judges want

**The brief:** an application, IntelliJ plugin, or ACP agent (connected via JetBrains Air) whose main goal is making developers' lives easier. It must be an AI solution. Integrating with JetBrains tools is "a great idea" but not required. They want a good idea with a proof of concept.

**Judging, in priority order:**
1. **Implementation / proof of concept:** does it run end to end, not a mocked screenshot?
2. **UX:** does it fit naturally into an existing workflow?
3. **Innovation:** something beyond "wrap a prompt around a common IDE action".
4. **Agentic development process:** did agents in JetBrains Air do real work while you reviewed and steered? Extra credit for multi-agent workflows.
5. **Presentation:** a clear demo, and our design decisions explained.

## How this pitch is different from the others

- **The user is a developer, not a security reviewer.** Specifically, a hardware developer editing Verilog, or reviewing an AI's edit to it.
- **The hero is the AI editor,** not the decompiler: hover a block → ideas → Apply → the Verilog changes → Yosys says **"proven equivalent"** or **"behaviour changed"**, with the input that differs. Yuyao is building it now.
- **The IDE integration is shown, not claimed:** Decap's tools inside JetBrains Air/Junie through MCP.
- **Criterion 4 gets its own beat:** our multi-agent board.
- **Results-wall numbers are a one-liner,** and the judge's-number trigger is optional.
- **Minecraft is the end of the developer loop:** edit → prove → build → test on levers.

## Readiness gates (decide on the day)

| Piece | Owner | Status | Rule |
|---|---|---|---|
| **AI editor** (hover → ideas → Apply → Yosys review) | Yuyao | Building: issue #68, branch `68-ideas-editor` (taken over 00:26; confirmed on the board at 00:34) | **Plan A only after one full rehearsal.** Otherwise Plan B |
| **Witness drawer** (`A`): Explain / Suggest edit / Comment on Decompile lines | Ian, on main (#65) | Tested live on `cla_4bits`, all 4 flows work | Plan B hero |
| **Decap MCP in JetBrains Air/Junie** (`decap mcp`) | Alex or Ian | MCP server works (13 tools against the real core). **Not yet connected in Air** | Connect once, record 20 s. If it fails, cut it and say "MCP, works in Claude Code" |
| **Build in Minecraft** from the Decompile page | Yuyao, on main (#60) | Works (Verilog path checked 40/40) | Keep |

Known agent quirk (Yuyao, #65): on a small output cone the agent proposed the same true XOR claim 5 times and got "restated" each time. Pick a demo region where this doesn't happen, and rehearse it.

## The hook (0–15 s)

> "AI agents are starting to write chip code. Siemens, Cadence and Synopsys have all announced them. But you'd never merge a pull request you can't check, and you can't patch a chip after it ships. So we gave the agent a referee: every change it makes is formally proven, or it comes back with the exact input that breaks it."

## Plan A script (editor rehearsed), 90 s

| Time | Beat | On screen | Say |
|---|---|---|---|
| 0–15 s | **Hook** | Decompile page of a real chip | The hook |
| 15–25 s | **Context, fast** | Decompiled Verilog beside the author's code, "Proven equivalent" | "Decap turns any chip's netlist back into readable Verilog, proven exactly right. 15 of 18 real chips we never tuned on. That's our starting point." |
| 25–55 s | **The editor** | Hover a block → conversation → "Generate ideas" → pick one → Apply → the Verilog changes → Yosys review | "Hover any block and ask the agent for ideas. Apply one, and the change lands in the Verilog. Then the proof runs. If behaviour is unchanged: proven equivalent, safe to merge. If it changed, you get the exact input where it differs, before it's ever silicon." |
| 55–65 s | **In your IDE** | 10 s recording: JetBrains Air/Junie calling Decap's MCP tools | "Every Decap tool is an MCP tool, so the same checks run inside JetBrains Air or any agent you already use." |
| 65–80 s | **Test it physically** | "Build in Minecraft" → redstone builds in about 3 s → flip the levers | "And because the edited code is proven, we can build it as a real circuit, out of redstone. Your unit test has levers." |
| 80–90 s | **How we built it + close** | The board (GitHub issue #1) | "Built by three humans each steering their own coding agent, coordinated through one GitHub issue. **The agent proposes. The proof decides.**" |

## Plan B script (editor not rehearsed), 90 s

Same hook and close. Replace 25–55 s with the **Witness drawer** (`A`):
1. Click a line in the decompile → "Explain".
2. The agent proposes a one-line claim, and it's proven (green) or refuted with a counterexample (red).
3. "Suggest edit" shows the agent's proposal.
4. Say: "The agent can say anything. It only counts once Yosys proves it."

## The agentic-process story (criterion 4)

**What's true:**
- Three humans: Yuyao (lead: core, web, redstone), Ian (agent layer), Alex (Minecraft, web polish). Each steers their own coding agent: Claude Code, Codex.
- They coordinate through **one pinned GitHub issue** (`tools/board`). Agents tag each other by login, hand work over, and need the owner's OK before editing someone else's folder (`AGENTS.md`).
- **Real examples to show:**
  - Alex's agent measured in-world that trigger 77 is invisible on `uo_out[0]` (the honest sum bit is already 1). Yuyao's agent handed the fix back.
  - Yuyao's agent found Ian's branch had no shared history and would have undone the redesign, so it replayed only Ian's files onto main (#65).
  - Agents reviewed and merged each other's PRs (#53, #60, #61, #65).
- The Witness design itself came from a multi-agent design tournament: 6 designs, 5 judges, a red team (`docs/decap/witness-plan.md`).

**Be honest:**
- **Our agents were Claude Code and Codex, not JetBrains Air.** Say: "We built with Claude Code and Codex agents; Decap plugs into Air through MCP." Don't imply Air built the project.
- Humans steered. Mention a moment we rejected an agent's output (e.g. the unmergeable branch, the trigger bug).

## Likely questions

| They'll ask | We answer |
|---|---|
| "Isn't this a prompt wrapper?" | "The AI has no say in the verdict. Yosys proves every claim or edit against the circuit, for every input. The model is replaceable; the proof isn't." |
| "Which model?" | "Witness runs on DeepSeek for the bench; the guide is model-agnostic through pydantic-ai. The decompiler and proof use no AI at all." |
| "How does this fit my workflow?" | "It's MCP, so it's in whichever agent you already use: Air, Claude Code, Cursor. And there's a web app for reviewing." |
| "Why hardware?" | "Because a bug there is permanent. You can patch software tomorrow. A chip, never." |
| "Who's the user?" | "An engineer editing a block they didn't write, or reviewing an AI's edit to one." |

## Prep checklist

- [ ] Yuyao confirms whether the editor has been rehearsed (Plan A or B)
- [ ] Air/Junie + `decap mcp` recording (20 s), or cut that beat
- [ ] A demo region that doesn't trigger the "restated" loop
- [ ] The board open in a tab at a good example thread
- [ ] Minecraft server up; "Build in Minecraft" tested from the Decompile page
