# Fault Lines

**Track:** G-Research × HRT · **Council final mean:** 89.3 (Sonnet 91 · Opus 90 · Grok 87) · **Rank:** 1 of ~67

> After a big earthquake the faults that matter are invisible. We find them in the aftershock cloud, and then use them to forecast where the next damaging aftershock will hit.

## The vision
Seismologists already know aftershocks line up along faults, but operational aftershock forecasts spread risk in circles around the mainshock. Fault Lines makes the buried faults visible by fitting planes to a high-resolution relocated catalogue (the Ridgecrest QTM catalogue, ~10× more events than the standard one, literally from a paper called "Searching for hidden earthquakes"). It then feeds those planes into the forecast: risk spreads *along* the discovered faults. The reveal and the predictor are the same object, which is why every judge scored it 87+.

## Who it is for (in the demo story)
An emergency manager the morning after an M7: "I have N structural inspection teams. Which towns get them in the next 72 hours?"

## What the judges see

| Sponsor | What they get |
|---|---|
| G-Research | Hidden fault planes snap out of a point cloud in 3D under real terrain. Structured method (clustering + plane fitting), not decoration. |
| HRT | A forecast scored by out-of-sample log-likelihood against the standard isotropic model, a reliability diagram, and a cost-weighted decision (team allocation) scored on held-out data. |

## The 90-second demo
1. **0–15s**: Mojave desert terrain in 3D. "July 2019, Ridgecrest, M7.1. Here are 30,000 aftershocks." Points rain in.
2. **15–35s**: Clustering runs; planes snap into place one by one and the cloud resolves into two crossing faults. "Nobody drew these. The data did."
3. **35–55s**: The probability cloud reshapes, from circles around the epicentre to ribbons along the faults. On screen: "log-likelihood gain vs standard model: +X on held-out days".
4. **55–75s**: Switch to Turkey 2023. Towns light up, 5 inspection teams are allocated by expected loss (P(strong aftershock nearby) × population), and the reliability diagram shows the probabilities are calibrated.
5. **75–90s**: "Bet against the model": a judge picks a town, we replay the next 72h and see who was right.

## How it works
```
SCEDC Ridgecrest QTM CSV ─┐
USGS ComCat (Turkey)     ─┼─► cluster (DBSCAN/HDBSCAN) ─► plane fit (PCA/RANSAC) per cluster
                          │                                  │
                          │                                  ▼
                          └─► lmizrahi/etas isotropic fit ─► fault-aligned spatial kernel
                                                             │   (anisotropic Gaussian along each plane)
                                                             ▼
                                         time-split log-likelihood vs isotropic (the gate)
                                                             ▼
                              town allocation by expected loss + reliability diagram
                                                             ▼
                               precomputed JSON ─► three.js / deck.gl scene (terrain + points + planes + cloud)
```

**Leakage rule (Grok's catch):** fit planes only on events *before* the forecast-test cutoff. Otherwise the test sees the future.

## Data and tools (all free)
- **Ridgecrest QTM relocated catalogue**: SCEDC, public ~4.3 MB file. Use the SCSN hypocentre for the M7.1 mainshock (QTM depth is poorly constrained; SCEDC says so).
- **USGS ComCat FDSN API**: no key. Turkey 2023 sequence plus the live feed for an optional second scene.
- **`lmizrahi/etas`** (Python, ETH): isotropic ETAS fit and time-split event log-likelihood.
- **Terrain**: SRTM / Mapzen terrain tiles. **Population**: GeoNames / WorldPop town points.
- **Front end**: three.js or deck.gl (TerrainLayer + PointCloudLayer + custom plane meshes).

## 24-hour build plan

| Hours | Work | Owner |
|---|---|---|
| 0–2 | Download QTM + Turkey catalogues; fit isotropic ETAS with `etas`; set the time split | Model |
| 0–4 | Terrain + point cloud scene with time scrubber | Front end |
| 2–6 | Clustering + plane fitting on pre-split events; fault-aligned kernel; **log-likelihood comparison** | Model |
| **6** | **GO/NO-GO**: fault-aware beats isotropic on Ridgecrest held-out log-likelihood? | Team |
| 6–12 | Planes + probability cloud in 3D; snap-in animation | Front end |
| 6–12 | Turkey: town allocation, expected loss, reliability diagram | Model |
| 12–18 | "Bet against the model" replay; polish; precompute everything to static JSON | All |
| 18–22 | Live USGS second scene (optional); pitch script; record backup video | All |
| 22–24 | Rehearse ×3, submit by 11:30 | All |

**Fallback if the gate fails:** ship the honest version (planes + isotropic forecast + decision) and say plainly that the fault-aware kernel didn't beat the baseline. The council scores that around 80–85.

## Risks
- The fault-aware gain is marginal → fallback above; lead with the visual and the calibration.
- Breadth (plane fitting + custom kernel + 3D + Turkey pipeline) → precompute everything; the front end only reads JSON.
- The Turkey catalogue is sparser than Ridgecrest → Turkey is the decision check, never the gate.

## Comparable past winners
- **ICHack HRT data prize (wildfire spread simulator):** physical process → forecast → decision. Same class, with a stricter baseline test.
- **The Git Bang (Contra × Builder.io grand prize):** a 3D reveal of a large public dataset. Our 3D has a scientific payload.

## Council notes
- Sonnet: "The planes are the predictive signal." Would score the fallback ~80.
- Opus: "The only idea I'd bet on to place first." Watch execution breadth.
- Grok: no remaining objection once planes are fit pre-split.
