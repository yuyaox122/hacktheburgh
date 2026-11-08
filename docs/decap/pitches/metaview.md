# MetaView pitch: best overall

**Prize:** $500 per team member. No set challenge: "show a creative project", any track or none.

Numbers and honesty rules: [`../PITCH.md`](../PITCH.md). If this file disagrees with it, `PITCH.md` wins.
Status: 8 Nov 2026, 09:15 (synced with main through #99 and the board's 08:33 decision: no held-out Witness number in any pitch). On stage and on slides the product is **bASIC**; code and folder names (`decap/`, the `decap` CLI, API paths) stay as they are.

## What the judges want

A creative project that clearly works. "Best overall" judges are generalists: they reward **ambition, a working end-to-end chain, and a moment they remember.** They won't know what a netlist is, so the pitch must work for someone who has never heard of Verilog.

## How this pitch is different from the others

- **Minecraft opens it and gets the most time** (about 30 s). It's the "wow" and the proof that the whole chain works.
- **The full chain is the story:** real chip → readable code → proof → the judge's number found → a working chip rebuilt in redstone. No other team will have that length of pipeline running live.
- **Plain language throughout.** Use the one-sentence version from `PITCH.md`. Numbers: one headline, nothing else.
- **The tiny FPGA in redstone** is the encore if time allows.
- **Tone: fun first, credible second.** The credibility line is "a formal checker proved it", said once.

## The one sentence (if someone asks "what is it?")

> "bASIC turns a computer chip back into readable code and proves the code is exactly right, so anyone can see what the chip really does, including anything hidden inside. Then we build the chip in Minecraft."

## The hook (0–15 s)

> *(over the Minecraft fly-through)* "This is a real computer chip: a design someone actually sent to the factory, rebuilt block for block in Minecraft. Software has View Source; you can read any web page. Chips don't. So we built View Source for silicon."

## The script (90 s)

| Time | Beat | On screen | Say |
|---|---|---|---|
| 0–15 s | **Hook** | `minecraft/out/flythrough.mp4`: the real layout as blocks, metal layers spread out, wires glowing | The hook |
| 15–30 s | **Read it** | Presenter mode (`D`), Read step: the decompiled code beside the author's own, "Proven equivalent" | "bASIC reads the chip's raw gates back into code a person can read, and a formal checker proves it's exactly the same circuit. It did that for 15 of 18 real chips it had never seen." |
| 30–50 s | **Your number** | Find step: the judge's number as a flagged line | "You picked a number. We hid a secret trigger for it inside this chip, and here it is: your number, as a line of code nobody wrote. That's how you'd catch a backdoor." |
| 50–80 s | **Build it** | Minecraft: two redstone builds of the same proven code, clean and planted. Flip the levers to the trigger value: the planted lamp lights, the clean one stays dark. Pan across the redstone | "Because the code is proven exactly right, it *is* the chip. So we compile it into real redstone: no command blocks, no cheats, about 33,000 blocks, checked against the original chip 60 cycles out of 60. Same chip, backdoor included." |
| 80–90 s | **Close** | Lamp lit | "You can't review what you can't read. **Now you can read silicon. You can even walk around in it.**" |

**Minecraft honesty:** the redstone copy is pre-planted with **78** until Yuyao's live "judge's number in Minecraft" has been rehearsed. Say: *"This one we planted earlier, with 78."* If the live version is ready, use the judge's own N here. It's the strongest possible ending for this track.

**If the Minecraft client fails:** the website's Minecraft page (`#/minecraft`) shows the same build in 3D with real Minecraft block models (repeaters, comparators, dust, levers, lamps), and the levers and lamps toggle live. This needs `npm run mc-assets` once on the demo laptop, and the page served from `npm run dev`: the textures are loaded in dev only and stripped from builds (Mojang's assets stay off the public site, #72), so the public site shows flat colours.

**Encore (Q&A, or if 10 s spare):** the tiny FPGA in redstone. It has 2 logic blocks, and "its 30-bit bitstream is a bank of levers: reprogram a chip by flipping switches". 126 primitives, 149k blocks.

## Why it's impressive (say it in Q&A, not in the 90 s)

- **Every link is real and checked:**
  - real Tiny Tapeout chips;
  - the decompile is formally proven;
  - the redstone is checked in vanilla Minecraft against the chip's netlist (sign_addsub: 200/200 vectors; cla_4bits: 60/60 cycles);
  - the layout world matches the reference simulation 30/30.
- **Measured honestly:** 18 held-out chips, split frozen before tuning; failures shown in red; both scored runs reported.
- **AI with a referee:** agents propose, a proof scores every claim. Show it live rather than as a number: hover any line and the agent explains it in plain English in a few seconds, read from code Yosys already proved; or run the red-team drill (a secret trigger, found blind, about 10 s).
- **Built by agents, steered by humans:** three people, each with their own coding agent, coordinating through one GitHub issue.

## Likely questions

| They'll ask | We answer |
|---|---|
| "Is the Minecraft chip real?" | "Yes. The layout world puts every gate where it sits on the real chip. The redstone version is compiled from the proven code and checked cycle by cycle against the real netlist." |
| "Can it read any chip?" | "Small open chips today, up to a few hundred gates. It reads design files, not manufactured chips." |
| "What's the real-world use?" | "Chips are built from parts you didn't make, and AI is starting to design them. You can patch software, but never a chip. bASIC lets you read and check what you're about to ship." |
| "Did AI write this?" | "Our coding agents helped build it, steered by us. There's no AI in the decompiler or the proof. That's what makes the result trustworthy." |

## Don't say

- "First ever." Hardware decompilers exist (Sisco 2023, Subramanyan 2014). Our edge is whole chips proven, blind, live, and in Minecraft.
- That Minecraft is running the judge's number when it isn't.

## Prep checklist

- [ ] `npm run mc-assets` run once; web served with `npm run dev` (real block textures in the Minecraft page fallback)
- [ ] Fly-through video cued; the Minecraft client at the lever bank, both copies settled (`/tick sprint`)
- [ ] Presenter mode on the judge's chip; planting returns within 15 s
- [ ] FPGA build pre-placed, if doing the encore
- [ ] One person narrates, one drives Minecraft
