# Minecraft

Two ways to put a real Tiny Tapeout 08 chip into Minecraft Java 1.21.11:

| Folder | What it does |
|---|---|
| `redstone/` | Compiles the chip's netlist, or any small Verilog, into real redstone (repeaters, comparators, dust; no command blocks). Every build is checked in a vanilla server. The web app's Minecraft page talks to it through `bridge.py`. Details: [redstone/README.md](redstone/README.md). |
| `world/` | Draws the chip's real layout (GDS) as blocks and runs its logic as a datapack: levers drive the inputs, lamps show the outputs, every gate and wire lights up while its signal is 1. |
| `common/` | Shared by both: RCON client, the vanilla server, chip and PDK data, the netlist reader, Liberty cell functions, datapack helpers. |

It is one Python package. Run everything from the repository root as `python -m minecraft.<half>.<script>`.

## Setup

- **Python 3.10+.** No packages needed, except KLayout for `world/` (`pip install klayout`).
- **`gh`, logged in.** Chip netlists, GDS and the SKY130 Liberty JSON are fetched from GitHub on first use into `decap/data/`, the same cache the decompiler uses. Set `MINECRAFT_DATA_DIR` to use another folder.
- **Yosys** for `redstone/` (`brew install yosys`, or `pip install yowasp-yosys` plus a `yosys` shim). **iverilog** is optional: the bridge and `check.py vchip` use it to compare against the Verilog source.
- **Java 21.** Found in this order: a JDK unpacked as `minecraft/tools/jdk-21*/` (Windows), Homebrew's `openjdk@21`, then `java` on `PATH`.
- **The server jar.** Put Mojang's vanilla 1.21.11 `server.jar` at `minecraft/tools/server-1.21.11.jar`. It is not committed; running it means accepting the [Minecraft EULA](https://aka.ms/MinecraftEULA).

Servers keep their worlds in `minecraft/server/<name>/`. Builds go to `minecraft/redstone/out/` and `minecraft/world/out/`. All of these are gitignored.

| Server | Folder | Game port | RCON | Used by |
|---|---|---|---|---|
| check | `server/check/` | 25566 | 25576 | `redstone.check` (ticks frozen and stepped) |
| demo | `server/demo/` | 25565 | 25577 | `redstone.demo`, `redstone.bridge` (the playable world) |
| world | `server/world/` | 25565 | 25575 | `world.server`, `world.check`, `world.showcase` |

`demo` and `world` share game port 25565, so run one of them at a time. The redstone servers take `REDSTONE_SERVER_DIR`, `REDSTONE_RCON_HOST`, `REDSTONE_RCON_PORT` and `REDSTONE_GAME_PORT` from the environment.

## Run it: redstone

```sh
python -m minecraft.redstone.chipdata fetch tt_um_sign_addsub   # netlist, GDS, Liberty into decap/data/
python -m minecraft.redstone.sim tt_um_sign_addsub              # techmap check, no Minecraft needed
python -m minecraft.redstone.pnr tt_um_sign_addsub              # -> minecraft/redstone/out/<chip>/layout.json
python -m minecraft.redstone.mc start                           # headless check server
python -m minecraft.redstone.check chip tt_um_sign_addsub 50    # build it in vanilla, compare with the netlist
python -m minecraft.redstone.demo start                         # playable server on localhost:25565
python -m minecraft.redstone.bridge                             # http://127.0.0.1:8002/redstone/ for the web app
```

Then `cd web && npm run dev` and open `#/minecraft`. Vite proxies `/redstone` to the bridge.

## Run it: world

```sh
python -m minecraft.world.build tt_um_Electom_cla_4bits --plant 137   # -> minecraft/world/out/<chip>/decap_chip
python -m minecraft.world.server setup tt_um_Electom_cla_4bits        # minecraft/server/world/, datapack installed
python -m minecraft.world.server run                                  # blocks; use a second terminal for the rest
python -m minecraft.world.server rcon "function decap:build"          # draw the chip (or run it in game)
python -m minecraft.world.check tt_um_Electom_cla_4bits -n 40         # in-game chip vs the Python reference
python -m minecraft.world.showcase                                    # add two redstone builds next to it
```

In game: Multiplayer → `localhost`, then `/function decap:view` (the lever panel) or `/function decap:overview`. `python -m minecraft.world.build <chip> --install <world folder>` copies the datapack into any world.

`world/client.py`, `world/fly.py` and `world/shot.ps1` are Windows tools: they launch the game client, then record a fly-through of the chip with ffmpeg.
