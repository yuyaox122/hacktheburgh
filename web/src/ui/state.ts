// Cross-page app state and hooks (kept tiny on purpose).

import type { ChipResult } from "../api/types";

/** A page that shows decompiled Verilog the agent can decorate (the Decompile page). */
export interface CodeHost {
  readonly chip: string;
  readonly el: HTMLElement;
  readonly result: ChipResult | null;
  highlightFromGuide(h: { cells?: string[]; lines?: number[] }): void;
}

export interface PaletteCommand { label: string; key?: string; run: () => void }

export const app: {
  route: string;
  askGuide?: (q: string) => void;
  guideContext?: () => unknown;
  guideHighlight?: (h: { cells?: string[]; lines?: number[] }) => void;
  pageCommands?: () => PaletteCommand[];
  onCodeRendered?: (page: CodeHost) => void;
  planted: Map<string, Promise<ChipResult>>;   // key `${chip}:${n}` -> plant result (started early by the demo prep)
  judge: { chip: string | null; n: number | null };
} = { route: "stage", planted: new Map(), judge: { chip: null, n: null } };
