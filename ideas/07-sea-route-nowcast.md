# Sea-Route Nowcast: "Channel Window"

**Proposed by:** Ian (#6) · **Track:** G-Research × HRT (Channel only)
**Council:** as written, G-R × HRT **71.0** (66 / 72 / 75), Wordsmith 65.7 → after hackathon office hours **79.0** (Sonnet 79 · Opus 78 · Grok 80)

> There's an invisible "crossing window", a combination of sea state and the number of calm days since the last storm, that releases small-boat crossings. We learn it from eight years of data, watch it open in the forecast, and use it to put rescue capacity in place before boats leave.

## The vision
Weather obviously matters to Channel crossings, but nobody publishes the *shape* of that dependence: how wave height, wind and pent-up demand after storms combine. Channel Window learns that response surface from the Home Office daily series and archived marine forecasts, renders it as a 3D surface, and plots each of the next 7 forecast days as a point climbing into or out of the window. The same surface is the forecasting model, and it drives a cost-loss decision on pre-positioning rescue and reception capacity at Dover.

## Who it is for
Rescue coordinators (HM Coastguard / RNLI) and reception planners. A humanitarian readiness decision, aggregates only.

## What the judges see

| Sponsor | What they get |
|---|---|
| G-Research | The crossing window as a 3D surface; forecast days moving through it; an animated Channel sea-state field. |
| HRT | Walk-forward backtest vs seasonal-naive and weather-free baselines, calibrated P(high-crossing day), and a cost-loss pre-positioning rule with a backtest of decisions. |

## The 90-second demo
1. **0–15s**: The Channel, with an animated sea state. "Eight years of daily crossings. Here's what the data says about weather."
2. **15–40s**: The 3D window surface rises: crossings vs wave height × wind × calm-day streak. "After a storm, the first calm night releases the backlog."
3. **40–60s**: The next 7 days appear as points; day 3 climbs into the window. Forecast ribbon with intervals.
4. **60–80s**: Decision: "P(high-crossing day in 2 m+ sea) = 0.82, so pre-position", with expected value across thresholds and the backtested decisions.
5. **80–90s**: Backtest and calibration slide; ethics slide (aggregates, humanitarian use only).

## How it works
```
UK Home Office small-boats daily series (ODS, SB_01, 2018-01-01 → 2026-11-05)
Open-Meteo Historical Forecast API (archived runs; MFWAM waves from Oct 2021) at the Dunkirk–Calais coast
        ▼
features: overnight wave height / swell period, wind speed + direction, calm-day streak, lags, seasonality
        ▼
LightGBM Poisson (quantile intervals), walk-forward from 2022  ─► baselines: seasonal-naive, weather-free, trend
        ▼
partial dependence = the 3D "window" surface (one object: picture = model)
        ▼
calibrated P(high day ≥ p90) and P(high day in 2 m+ sea) ─► cost-loss pre-positioning rule
        ▼
deck.gl sea-state field + three.js surface + 7-day ribbon (every count point carries its Home Office source quote)
```

### Council build notes (important)
- **Archived wave forecasts only start in October 2021** (MFWAM via Open-Meteo); Open-Meteo's archive generally starts 2021–22. The leakage-free backtest is therefore ~2022–2026, not 2018. Print the forecast lead time on the slide (Grok, Opus).
- If archived marine variables aren't available for the point, use **wind-only archived features rather than reanalysis waves**. Reanalysis reintroduces leakage (Opus).
- Include a **trend baseline**: people per boat rose from ~7 to ~70 and enforcement changed over time, so weather can lose to a trend, or win for the wrong reason (Grok).

## Data and tools
Home Office small-boats time series (gov.uk ODS) · Open-Meteo Historical Forecast + Marine APIs (no key) · Python (polars, LightGBM) · deck.gl / three.js.

## 24-hour build plan

| Hours | Work |
|---|---|
| 0–2 | Parse the ODS into a daily series; pull archived forecasts for the departure coast; check marine coverage |
| 2–6 | Features + LightGBM Poisson; walk-forward vs seasonal-naive / weather-free / trend |
| **6** | **GO/NO-GO**: weather features beat the weather-free and trend baselines in walk-forward |
| 6–12 | 3D window surface from partial dependence; 7-day points moving through it |
| 8–14 | Calibration, cost-loss rule, decision backtest |
| 12–18 | Channel sea-state field; forecast ribbon; source quotes on points |
| 18–24 | Ethics slide, pitch, backup video, rehearse, submit |

**Cut:** Italy, Spain, Frontex harmonisation, the policy sub-agent, and the Wordsmith framing. **Fallback:** honest seasonal model + window visual.

## Risks
- "Calm sea → more crossings" is widely known, so the G-Research half is the weak half (all three judges). The surface's *shape* (the calm-streak release) has to be the surprise.
- Policy and enforcement shocks that weather doesn't capture.
- Political sensitivity in front of sponsors → humanitarian framing, aggregates only, explicit ethics slide.

## Comparable past winners
- **ICHack HRT prize (wildfire spread simulator):** weather-driven forecast → readiness decision. Same class.

## Team-level note
Now an honest HRT entry (79), but below Fault Lines (89.3) on both halves. Sonnet's best upgrade: show the same learned surface holding out of sample on a second period, so the pattern generalises rather than fitting once.
