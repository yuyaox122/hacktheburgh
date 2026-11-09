// Cross-page app state and hooks (kept tiny on purpose).

import type { ChipResult } from "../api/types";
import type { CodeHost } from "../agent/codeAgent";

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
  llm: boolean | null;
  api: boolean | null;
} = { route: "stage", planted: new Map(), judge: { chip: null, n: null }, llm: null, api: null };
