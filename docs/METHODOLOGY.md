# Methodology: Second-round projection (President 2026)

Reference document on how the model was built, how data enters, which statistical model is used and what the parameters are. It was written from the code in the project root and from the logs of the 2026-10-25 count. Where something is interpretation and is not in the code, it is marked **(inferred)**.

Conventions: R1 / R2 = first / second round (in code and filenames, `t1` / `2t` stand for the same). TSE = Superior Electoral Court, which publishes the results. UF = federative unit (state; `zz` = voters abroad). A *polling section* (seção) is the unit that reports a ballot box; an *electoral zone* (zona eleitoral) groups sections inside a municipality. Percentages use decimal points.

---

## 1. Goal

Estimate, **during the count** of the second round, the final percentage of valid votes of candidate **A (13, Lula)** against **B (22, Flávio)**, with a 90% interval and the probability that A wins.

The challenge: the partial count is **not a random sample**. The sections that report first come from specific places and profiles, so the raw count (`cnt`) is biased. The model corrects this by anchoring each municipality on its own R1 result.

Core idea: the R2 result of each municipality is predicted from the R1 result of the same municipality. The model is trained on municipalities that are already well counted and extrapolates to the rest.

## 2. Pipeline overview

```
 TSE Open Data (R1 zip) ──► scenario_t1.py ──► cenario_municipio.csv  (prior / R1 per municipality)
                                                        │
 TSE results (R2 JSON) ──► projection_r2.py  ◄──────────┘
        (gate + sample)         │  model + bootstrap
                                ├──► historico.csv        (1 row per cycle)
                                └──► ultimo_snapshot.csv  (per sampled municipality + "_resto" per UF)
                                             │
                            dashboard_html.py ◄──┘  + br_uf_svg.json (map)
                                └──► dashboard.html (reloads itself)
```

| Step | Script | Input | Output |
|---|---|---|---|
| 1. R1 base | `scenario_t1.py` | `dados_tse_2026/munzona_2026.zip` | `cenario_zona.csv`, `cenario_municipio.csv` |
| 2. Map | `gerar_br_uf_svg.py` | `dados_geo/brazil-states.geojson` | `br_uf_svg.json` |
| 3. R2 ingestion + model | `projection_r2.py` | `cenario_municipio.csv` + TSE API | `historico.csv`, `ultimo_snapshot.csv` |
| 4. Dashboard | `dashboard_html.py` | the two CSVs above + `br_uf_svg.json` | `dashboard.html` |
| Evaluation | `docs/error_by_cycle.py` | `data/election_night_2026-10-25/historico.csv` | `docs/error_by_cycle.md`, `docs/error_by_cycle.png` |
| Diagnostics | `diag_overestimation.py`, `diag_pst.py` | snapshot + history | terminal text |
| Test | `test_synthetic.py` | `cenario_municipio.csv` (simulates the TSE) | terminal text |
| Legacy | `legacy_state_swing.py` | `cache/uf_*.json` | terminal text |

## 3. TSE identifiers

- Year `2026`, office `1` (President).
- R1 election: `6257`. R2 election: `6258` (`--e2 6258` is a required argument of `projection_r2.py`; `6257` appears only in the name of the cache file `t1_6257_c1_13_22.parquet`).
- Candidates: `--A 13`, `--B 22`.
- Units: 27 UFs + `zz` (abroad) = 28 "UFs"; 5,757 municipalities in total (abroad enters as units of its own).

## 4. Data ingestion

### 4.1 First round (offline, once): `scenario_t1.py`

- Source: `votacao_candidato_munzona_2026_BR.csv` inside `munzona_2026.zip` (~452 MB), downloaded from the TSE Open Data portal. CSV with `;` separator, `latin1` encoding.
- Filter: `NR_TURNO == 1` and `CD_CARGO == 1`.
- Metric: `QT_VOTOS_NOMINAIS_VALIDOS`. For each row: `A1` (if candidate 13), `B1` (if 22), `O1` = the rest (all other candidates).
- Keys: `uf` (lowercase), `mu` (municipality code, zero-padded to 5 digits), `zona` (electoral zone).
- Aggregated at two levels: per zone and per municipality. Columns: `A1, B1, O1, V1 = A1+B1+O1, h2h_A = A1/(A1+B1)`.
- R1 totals (municipality level): A1 = 53,879,538; B1 = 56,104,503; O1 = 9,316,747.

**Simple transfer scenario** (used as a reference, not as the main model):

```
share_A(theta) = (A1 + theta·(1-loss)·O1) / (A1 + B1 + (1-loss)·O1)
```

- `theta` = fraction of the R1 "other" votes that goes to A. Default grid `0.3, 0.4, 0.5, 0.6`.
- `loss` (`--loss`) = fraction of the "other" votes that becomes abstention/null in R2. Default `0.2`.
- The script also prints the tie `theta` (50%).
- On the dashboard this band becomes the yellow "R1 scenario" lines (`--cen min,max`).

### 4.2 Second round (live): `projection_r2.py`

Source: `https://resultados.tse.jus.br/oficial/ele2026/6258/dados/...`. These are "-u" (unique) JSON files:

- UF or BR aggregate: `.../{uf}/{uf}-c0001-e006258-u.json`
- Municipality: `.../{uf}/{uf}{mu}-c0001-e006258-u.json`
- Fields read: `carg[].agr[].par[].cand[]` with `n` (number) and `vap` (counted votes); `s.pst` (% of sections counted); `hg`/`idg` (publication stamp).
- Decimal commas are converted (`num()`).
- Each URL carries `?nocache=<ms>` to avoid caching.

**Collection cycle** (`cycle`):

1. **Gate**: 29 requests = 27 UFs + `zz` + `br`. Computes `div_br = (sum of UFs − BR) / BR`.
2. The cycle is flagged **inconsistente** (and produces no projection) if any UF file is missing or if the sum of the UFs exceeds 102% of BR. This happens when UFs are published out of sync. Lagging UFs (`div_br < 0`) are accepted.
3. **Sample of 400 municipalities** (~429 requests per cycle in total). Municipalities that already reached `pst >= 99.9` are not fetched again.
4. Runs the model and writes `historico.csv` and `ultimo_snapshot.csv`.

**`--watch` mode** (the one used during the count): polls only the BR JSON every `--poll` (60 s). When `(idg, hg)` changes, it runs a full cycle. An inconsistent or failed cycle repeats every `--retry` (300 s). Without `--watch`, it loops every `--loop` (180 s).

**Block protection**: concurrency `--conc 3`, 3 attempts per URL, 20 s timeout. HTTP 403/429 waits 60 s; three in a row abort the round and the process sleeps 5 min. 404 becomes `None` (no retry).

### 4.3 Sample design (`pick_sample`, seed 7)

1. The **100 largest** by `V1` enter with certainty.
2. **150 small** ones (`V1 < 20,000`) drawn with equal probability, allocated by region (largest remainder).
3. The rest (150) drawn among the others, stratified by UF proportionally to `V1`.

The sample is fixed during the whole count (same seed), so the historical series is comparable. The size is set with `--sample-size` (default 400).

Regions (`REG`): N, NE, CO, SE, S, and EX (abroad).

### 4.4 Map and dashboard

- `gerar_br_uf_svg.py` converts `dados_geo/brazil-states.geojson` into SVG paths: equirectangular projection with `cos(-15°)` correction, width 600, simplification 0.04°, drops islands smaller than 0.05 square degrees. Run once.
- `dashboard_html.py` reads `historico.csv` + `ultimo_snapshot.csv` and writes a self-contained `dashboard.html` (Chart.js 4.4.1 via cdnjs), with a `meta refresh` every `--every` (30 s). It ignores `inconsistente` cycles.

## 5. The model

### 5.1 Response and predictors

Per municipality `i` (already with R2 votes):

- `y = logit((A2 + 0.5) / (A2 + B2 + 1))`, that is, the share of A among A+B in R2, with smoothing.
- `x1 = logit((A1 + 0.5) / (A1 + B1 + 1))`: head-to-head (h2h) of A in R1.
- `x2 = O1 / V1`: weight of the "other" votes in R1.
- `x1·x2`: interaction.
- `zc = log(V1) − mean(log V1)`: municipality size, centered.
- Fixed effect of **UF** (28 levels) and of **region** (6 levels), both shrunk.

Logits are clipped to [1e-4, 1−1e-4].

### 5.2 Fit: ridge WLS (`fit`)

```
beta = (XᵀWX + P)⁻¹ XᵀW y
```

- Weights: `w = V2 · (pst/100)²`. Municipalities that are more counted and larger weigh more, and barely counted ones weigh very little.
- Diagonal penalty `P`: 1e-6 on the fixed coefficients (practically free); `lam_uf = 25 · mean(w)` on the UF effects; `lam_reg = 10 · mean(w)` on the region effects.
- **Training set**: sampled municipalities with `V2 > 0` and `pst >= 50`. If fewer than 30, uses `V2 >= 30`. If still fewer than 30, the cycle does not project ("too little data").
- After the fit, `p_hat` is computed for **all** 5,757 municipalities (sampled or not).

### 5.3 How much is left to count (`rho`, `Vhat`, `R`)

- `rho = V2 / V1` (turnout relative to R1) measured in **closed** municipalities (`pst >= 99.9`): median per UF, falling back to the national median (if there are ≥ 20 closed) or `0.97`.
- Sampled municipality: `Vhat = V2 + (1 − pst/100) · V1 · rho`; `R = Vhat − V2` (expected remaining votes).
- UF: `R_uf = (1 − pst_uf/100) · V1_uf · rho_uf`.
- Non-sampled municipality: weight `wt = V1 · rho · (1 − pst_uf/100)`.
- Correction: if the sum of the sampled municipalities of a UF exceeds the UF's official aggregate (lagging aggregate), `max(official, sample sum)` is used (counter `n_adj`).

### 5.4 Point estimate

For each UF, the expected share of the remaining votes is the mean of `p_hat` weighted by `wt`. The national projection is:

```
point = (ΣA_counted + Σ_uf R_uf · share_uf) / (Σ(A+B)_counted + ΣR_uf)
```

That is: **votes already counted (official, per UF) + remainder estimated by the model**. The official count is never "modeled", only what is missing.

### 5.5 Interval and probability (bootstrap by UF)

`n_boot = 300`, seed 0. In each replicate:

1. Resamples **UFs** (with replacement) among those in the fit, and refits the model (cluster bootstrap).
2. Adds a random shock per UF: `N(0, tau · sqrt(rem_frac))`, where `rem_frac = R / Vhat` of the UF. Nearly closed UFs get little shock.
3. Recomputes the national share.

- `lo`/`hi` = 5% and 95% quantiles of the simulations; `pA` = fraction of simulations with share > 0.5.
- `tau` = standard deviation of the UF effects estimated across the UFs in the fit, with a floor:
  - `0.05` if there is data in ≥ 15 UFs;
  - `0.10` if < 15 UFs have data;
  - `0.15` if the fit is **not anchored** (fewer than 30 mature municipalities with `pst >= 50`). The `status` becomes `nao_ancorado`.
  - Can be overridden by `--tau-min`.

Practical consequence: at 99.65% counted the interval is practically degenerate (51.77% to 51.77%) because `rem_frac ≈ 0` and the `tau` floor has almost nothing to multiply.

## 6. Parameters (summary)

| Parameter | Value | Where |
|---|---|---|
| Candidates A / B | 13 / 22 | `--A`, `--B` |
| R2 election / office | 6258 / 1 | `--e2`, `--cargo` |
| Sample | 400 (100 largest + 150 small + 150 stratified) | `--sample-size`, `pick_sample` |
| Small | `V1 < 20,000` | `pick_sample` |
| Sample / bootstrap seed | 7 / 0 | code |
| Concurrency / timeout / attempts | 3 / 20 s / 3 | `--conc`, `get_json` |
| Poll (watch) / retry / loop | 60 s / 300 s / 180 s | `--poll`, `--retry`, `--loop` |
| 403/429 block | waits 60 s, aborts after 3, sleeps 5 min | `get_json`, `watch` |
| Gate tolerance | UFs > 102% of BR = inconsistent | `cycle` |
| `lam_uf` / `lam_reg` | 25 / 10 (× mean of the weights) | `fit` |
| Fit weight | `V2 · (pst/100)²` | `fit` |
| Training | `pst >= 50` (fallback `V2 >= 30`, minimum 30) | `project` |
| `rho` fallback | 0.97 | `project` |
| "Closed" municipality | `pst >= 99.9` | `project` |
| Bootstrap | 300 replicates, by UF | `project` |
| CI | 90% (5–95 quantiles) | `project` |
| Minimum `tau` | 0.05 / 0.10 / 0.15 | `project` |
| R1 scenario: `loss`, `theta` | 0.2; 0.3–0.6 | `scenario_t1.py` |
| Dashboard: refresh | 30 s | `--every` |
| Swing per UF on the map | ≥ 3 municipalities and ≥ 5,000 votes | `swing_uf` |

## 7. Outputs

**`historico.csv`**: one row per cycle: `ts, point, lo, hi, cnt, pA, apurado, n_fit, tau, status, div_br`. `status` ∈ `ok`, `nao_ancorado`, `inconsistente` (in the last case only `ts`, `status` and `div_br` are filled). If an old file has no `div_br`, `log_hist` rewrites it with the new column.

**`ultimo_snapshot.csv`**: `uf, mu, A1, B1, A2, B2, Vhat, R, p_hat, pst` per sampled municipality, plus one row with `mu = "_resto"` ("the rest") per UF that aggregates the non-sampled ones and closes to the UF official count.

**Where the files live.** The projector writes `historico.csv` and `ultimo_snapshot.csv` to the current directory (git-ignored, so a new run cannot overwrite the record). The 2026-10-25 election-night record is a copy under `data/election_night_2026-10-25/` (`historico.csv`, `ultimo_snapshot.csv`, `logs/watch.log`, original names kept, tracked in git). The log is the raw terminal output of that night and is kept as recorded, in Portuguese. `docs/error_by_cycle.py` reads that folder by default (optionally `python docs/error_by_cycle.py <data_dir>`).

## 8. How it evolved (timeline)

Times are from 2026-10-25 (runoff day), local time.

- **17:22 to 17:35**: first version of `projection_r2.py`, ridge WLS logit regression per municipality anchored on R1, with bootstrap by UF. Validated on synthetic data with biased arrival (`test_synthetic.py`).
- **17:35 to 17:45**: the municipal endpoint `-v.json` returned 404; replaced by `-u.json`, with the schema rewritten (`carg[].agr[].par[].cand[]`). Found that the municipal R2 **has no breakdown by zone**. The TSE publication stayed frozen at 0.25% since ~17:07 (polling stations open until 19:00 in part of the country).
- **17:48**: HTTP 429 block per IP, caused by the national sweep (5,570 municipalities, 12 connections, aggressive retry). This led to the municipality sample and the 403/429 protection. Also fixed a bug in the R1 cache key (it did not include `--ufs`).
- **~18:00**: R1 now comes from the Open Data portal (`cdn.tse.jus.br`, no rate limit): `scenario_t1.py` and the transfer scenario (tie theta 0.65).
- **18:05**: `legacy_state_swing.py` gives Lula 59.2% with 0.25% counted. **Discarded**: it compared the first R2 sections with the whole UF in R1, PA weighed 83% of the swing and ~87% of the votes counted in BR did not appear in the UF files. Lesson: swing is only valid between the same unit in both rounds.
- **18:10 to 18:50**: sample mode (**design A**: 150 largest + stratified by UF proportional to V1), UF vs BR gate (2%), `log(V1)`, region + UF with ridge, training on `pst >= 50` with fallback, `--watch` triggered by the BR stamp.
- **19:03**: first real cycle (0.4% counted, `n_fit` 31).
- **~19:10**: gate changed. The UF vs BR divergence stops blocking (UFs lagging behind BR froze the projection) and becomes monitoring only (`div_br`); a cycle is inconsistent only if UFs > 102% of BR or a file is missing. `--retry` from 300 to 90 s. (The current code default is 300 s; confirm which value was in effect during the run.)
- **19:15 to 19:30**: projection of ~52.5% to 53% with a uniform gain of ~4 pp in all UFs. Diagnosis: 0 of 400 municipalities closed; fit only on early sections; UF and region effects ~0; narrow CI due to lack of estimable heterogeneity, not precision. Test with `lam_reg = 0.1`: +0.22 pp, does not fix it. Kept 10. A terminal diagnostic at about this time showed an `x2` coefficient of about +1.1 (a terminal observation, **not reproducible**: only the last snapshot was kept; see §9.2, item 5).
- **19:39:19**: sample **design B** (100 largest + 150 small with V1 < 20k, equal probability by region + stratified), to have municipalities closing early and anchor the fit. Status `nao_ancorado` (`tau_min` 0.15 with < 30 mature). The snapshot gains the `pst` column.
- **19:40**: 10.9% counted, 54 mature, projection 51.3%. Swing in the 21 closed municipalities: +1.95 pp.
- **20:51**: per-UF table with the model converged in UFs with 89% or more counted; RJ with 27% counted and a swing of +4.0 pp, under suspicion of selection inside the capital (see §10).
- **21:11**: 99.65% counted, projection 51.77% against count 51.75%.

Supporting files of this phase: `diag_overestimation.py`, `diag_pst.py`, `dashboard_html.py` and `br_uf_svg.json`. Detail of sample design A vs B in §9.

## 9. Result observed on 2026-10-25

**Main finding:** a stable ~0.65 pp underestimate of the remaining vote from ~20% counted onward; leading hypothesis (untested): uniform allocation of pending votes across non-sampled municipalities within each UF (§10). This is a hypothesis, not an established cause.

**Reference "final".** The official result has not been incorporated yet. Until it arrives, the proxy for the final is the **raw count of the last ok cycle (99.65% counted): 51.7477% for A**. At that stage the remainder is small, and the projection of the same cycle (51.77%) differs from the count by 0.02 pp. When the official result is out, just change `OFFICIAL_FINAL` in `docs/error_by_cycle.py` and rerun.

The table and plot are generated by `docs/error_by_cycle.py` from `data/election_night_2026-10-25/historico.csv` (85 `ok` cycles; the 7 `inconsistente` are left out). Error = projection minus reference final.

![Projection error by counted share](error_by_cycle.png)

### 9.1 Summary by counted band

| Counted band | Cycles | Mean error (pp) | Error min to max (pp) | Mean b (pp) | CI covered |
|---|---|---|---|---|---|
| 0% to 5% | 14 | +0.55 | -1.97 to +1.12 | +0.57 | 14/14 |
| 5% to 20% | 16 | -0.30 | -0.45 to +0.12 | -0.34 | 16/16 |
| 20% to 60% | 27 | -0.37 | -0.48 to -0.29 | -0.61 | 16/27 |
| 60% to 90% | 20 | -0.17 | -0.29 to -0.05 | -0.64 | 5/20 |
| 90% to 100% | 8 | -0.00 | -0.04 to +0.02 | +1.03 | 6/8 |

`b = error / (1 - counted)` is the implied bias in the share of the **remaining** vote (see §9.2, item 2). The "CI covered" column counts cycles of a single trajectory; see item 3 on how to read it.

### 9.2 Conclusions

1. **Overestimation at the start (up to ~3% counted).** With `n_fit` from 31 to ~90 municipalities and the first sections of each place, the fit was above the final: +0.5 to +1.1 pp between 1% and 3% counted. The first cycle (0.4% counted, `n_fit=31`) erred downward, -1.97 pp, with the projection at 49.8%.
2. **Stable bias in the remaining vote, not just "underestimation between 20% and 60%".** The national error decays mechanically with what is left to count: `error ≈ b · (1 - counted)`, where `b` is the bias (in pp) in the share of A **within the votes not yet counted**. Computing `b = error / (1 - counted)` per cycle (column `b` in §9.3, bottom panel of the plot), `b` stays at **about -0.65 pp** (between -0.57 and -0.77 pp on average per band) from ~20% to ~80% counted, and the model **did not correct this bias during the night**: the projection only got closer to the final because the remainder shrank. The drop of the absolute error to below 0.1 pp (in magnitude) from ~82% counted comes from the `(1 - counted)` factor, not from learning. Above ~90% counted, `b` loses meaning (the divisor tends to zero and the final proxy itself has an error of the same order), so it should not be read there. Before 20%, the sign changes (+0.57 on average up to 5%, -0.34 between 5% and 20%) because early overestimation is still mixed in. The pattern is consistent with large cities arriving late and with the synthetic test (biased arrival); the leading hypothesis for the mechanism (untested, not an established cause) is in §10. **Where the bias lives (measured).** The error splits exactly (the identity holds to 2e-16) into an error of the **share** of the remainder and an error of the **quantity** `R` (which enters through `rho`, since `R_uf` is proportional to `rho_uf`). Over the 64 cycles for which the official count could be read from `watch.log`, the quantity term was at most 0.0071 pp in absolute value (about 0.007 pp), and the model's assumed total stayed between -1.2% and +0.1% of the final reference total. So `b` is a **share bias**: its mean over those cycles is -0.544 pp, of which -0.540 pp come from the share term. Caveat: the reference total is the one assumed in the last cycle (0.35% still to count), not the official total; redo this with the TSE final. The leading hypothesis (uniform `wt` for non-sampled municipalities, §10) acts through `s_uf`, the expected share of the remainder (§5.4): it changes where the pending votes are placed, not how many there are, which is consistent with the measurement above.
3. **CI coverage, read as a single trajectory, and why it failed.** The 85 cycles are **one single, strongly autocorrelated trajectory** (each cycle reuses practically the same data as the previous one). Counting cycles in which the CI covered the final (57 of 85) is **not a coverage rate**: it is an observation of one night. What it shows is that, from 39.5% counted and up to ~84%, the final stayed outside the CI (only 3 of the 29 cycles in that window fall inside, in isolation). **Measured:** the CI half-width, `(hi - lo) / 2`, was about 1x the error between 25% and 50% counted (half-width / |error| = 1.08 at 25%, 0.93 at 50% and 0.80 at 80% counted). The reason is structural: the bootstrap (§5.5) measures **variance** (the refit on resampled UFs and the shock per UF), not **bias**. A persistent bias of the size of the CI half-width yields about 50% coverage (for an approximately normal estimate with bias equal to the half-width, the final falls inside only on one side of the interval). The CI cannot widen to cover a bias that the model does not represent. **The real coverage of the CI can only be measured in the backtest** (§11, item 7), with several trajectories. In the last 2 cycles the CI collapses to a point (51.77%) and the proxy is 0.02 pp off; that is the proxy, not a model error.
4. **Sample.** The "Sample" column marks the active design in each cycle: **A** until 19:37:47 and **B** from 19:39:19, the time the sample design was switched (A: 150 largest + stratified by UF; B: the current one in §4.3). At the switch, `n_fit` goes back from 150 to 30 because the new sample had no mature municipalities yet. The errors of -0.3 to -0.5 pp occur after the switch; the switch did not eliminate them.
5. **Early coefficients (`x2`).** Running `diag_overestimation.py` on the last snapshot (21:11, `n_fit` = 397) gives `x2` = -0.116 (with `x1` = 0.958 and `x1·x2` = -0.173). A terminal diagnostic at ~19:30 showed `x2` at about +1.1; that value is a terminal observation and is **not reproducible**, because only the last snapshot was kept. The difference is consistent with the early coefficients being artifacts of selection (the first sections to arrive, with few mature municipalities in the fit) **(inferred; the 19:30 fit cannot be rerun)**.

### 9.3 Error per cycle (all ok cycles)

| Time | Counted % | Sample | Projection A % | 90% CI | Error (pp) | b (pp) | CI covered? | n_fit |
|---|---|---|---|---|---|---|---|---|
| 19:03:48 | 0.4 | A | 49.78 | 45.93 to 54.29 | -1.97 | -1.98 | yes | 31 |
| 19:11:11 | 1.7 | A | 52.80 | 51.42 to 53.85 | +1.05 | +1.07 | yes | 71 |
| 19:12:21 | 1.8 | A | 52.87 | 51.24 to 53.84 | +1.12 | +1.14 | yes | 72 |
| 19:13:30 | 1.8 | A | 52.63 | 50.82 to 53.71 | +0.88 | +0.90 | yes | 72 |
| 19:14:40 | 1.8 | A | 52.64 | 51.14 to 53.71 | +0.89 | +0.91 | yes | 74 |
| 19:15:50 | 1.9 | A | 52.62 | 51.25 to 53.58 | +0.87 | +0.89 | yes | 75 |
| 19:16:59 | 2.1 | A | 52.29 | 50.85 to 53.48 | +0.54 | +0.55 | yes | 78 |
| 19:18:10 | 2.2 | A | 52.75 | 51.26 to 53.91 | +1.01 | +1.03 | yes | 79 |
| 19:19:22 | 2.3 | A | 52.72 | 51.33 to 53.84 | +0.97 | +1.00 | yes | 85 |
| 19:20:36 | 2.5 | A | 52.69 | 51.00 to 54.00 | +0.94 | +0.97 | yes | 88 |
| 19:21:53 | 2.8 | A | 52.65 | 51.21 to 53.92 | +0.91 | +0.93 | yes | 91 |
| 19:23:58 | 3.3 | A | 52.05 | 50.87 to 53.23 | +0.31 | +0.32 | yes | 98 |
| 19:25:43 | 3.8 | A | 51.81 | 50.80 to 52.60 | +0.06 | +0.06 | yes | 103 |
| 19:27:25 | 4.5 | A | 51.93 | 50.84 to 52.60 | +0.18 | +0.19 | yes | 113 |
| 19:29:05 | 5.2 | A | 51.87 | 50.87 to 52.54 | +0.12 | +0.12 | yes | 118 |
| 19:30:39 | 5.7 | A | 51.82 | 50.92 to 52.56 | +0.07 | +0.08 | yes | 129 |
| 19:32:23 | 6.6 | A | 51.61 | 50.74 to 52.32 | -0.14 | -0.15 | yes | 137 |
| 19:34:16 | 7.6 | A | 51.46 | 50.61 to 52.15 | -0.29 | -0.32 | yes | 140 |
| 19:36:07 | 8.9 | A | 51.41 | 50.62 to 52.02 | -0.33 | -0.37 | yes | 146 |
| 19:37:47 | 9.6 | A | 51.36 | 50.52 to 51.94 | -0.39 | -0.43 | yes | 150 |
| 19:39:19 | 10.5 | B | 51.36 | 50.63 to 51.95 | -0.38 | -0.43 | yes | 30 |
| 19:40:50 | 11.2 | B | 51.30 | 50.72 to 51.87 | -0.45 | -0.51 | yes | 54 |
| 19:42:24 | 11.9 | B | 51.35 | 50.78 to 51.91 | -0.40 | -0.45 | yes | 60 |
| 19:43:59 | 12.8 | B | 51.33 | 50.79 to 51.90 | -0.42 | -0.48 | yes | 65 |
| 19:45:36 | 13.7 | B | 51.33 | 50.78 to 51.86 | -0.42 | -0.49 | yes | 67 |
| 19:47:14 | 14.7 | B | 51.42 | 50.95 to 52.02 | -0.33 | -0.39 | yes | 76 |
| 19:48:55 | 15.3 | B | 51.40 | 50.85 to 51.97 | -0.34 | -0.41 | yes | 80 |
| 19:52:36 | 16.1 | B | 51.41 | 50.86 to 51.99 | -0.34 | -0.41 | yes | 82 |
| 19:54:22 | 17.5 | B | 51.38 | 50.85 to 51.93 | -0.36 | -0.44 | yes | 88 |
| 19:56:01 | 18.6 | B | 51.39 | 50.87 to 51.93 | -0.36 | -0.44 | yes | 93 |
| 19:57:38 | 20.0 | B | 51.42 | 50.88 to 51.93 | -0.33 | -0.41 | yes | 99 |
| 19:59:15 | 21.2 | B | 51.41 | 50.91 to 51.93 | -0.34 | -0.43 | yes | 105 |
| 20:00:43 | 22.3 | B | 51.37 | 50.94 to 51.88 | -0.38 | -0.49 | yes | 107 |
| 20:02:16 | 23.8 | B | 51.27 | 50.79 to 51.78 | -0.48 | -0.62 | yes | 115 |
| 20:03:49 | 25.0 | B | 51.30 | 50.83 to 51.81 | -0.45 | -0.60 | yes | 119 |
| 20:05:19 | 26.5 | B | 51.33 | 50.87 to 51.84 | -0.42 | -0.57 | yes | 130 |
| 20:06:46 | 27.9 | B | 51.31 | 50.79 to 51.79 | -0.43 | -0.60 | yes | 138 |
| 20:08:14 | 29.3 | B | 51.31 | 50.78 to 51.78 | -0.44 | -0.62 | yes | 141 |
| 20:09:38 | 30.3 | B | 51.33 | 50.84 to 51.80 | -0.42 | -0.60 | yes | 144 |
| 20:11:04 | 31.9 | B | 51.33 | 50.85 to 51.78 | -0.42 | -0.61 | yes | 151 |
| 20:12:28 | 33.2 | B | 51.36 | 50.87 to 51.81 | -0.39 | -0.58 | yes | 163 |
| 20:13:58 | 34.9 | B | 51.37 | 50.89 to 51.79 | -0.38 | -0.59 | yes | 170 |
| 20:15:28 | 36.6 | B | 51.37 | 50.96 to 51.80 | -0.38 | -0.60 | yes | 178 |
| 20:16:58 | 38.1 | B | 51.36 | 50.95 to 51.77 | -0.39 | -0.63 | yes | 181 |
| 20:18:26 | 39.5 | B | 51.37 | 51.04 to 51.74 | -0.37 | -0.62 | no | 187 |
| 20:19:52 | 41.1 | B | 51.37 | 51.04 to 51.73 | -0.38 | -0.64 | no | 197 |
| 20:21:17 | 42.8 | B | 51.36 | 51.05 to 51.71 | -0.38 | -0.67 | no | 200 |
| 20:22:43 | 44.1 | B | 51.39 | 51.06 to 51.75 | -0.36 | -0.65 | yes | 203 |
| 20:24:13 | 46.0 | B | 51.40 | 51.09 to 51.74 | -0.35 | -0.65 | no | 210 |
| 20:25:46 | 47.2 | B | 51.42 | 51.10 to 51.75 | -0.33 | -0.63 | yes | 218 |
| 20:27:21 | 49.2 | B | 51.41 | 51.10 to 51.73 | -0.34 | -0.67 | no | 226 |
| 20:28:52 | 51.1 | B | 51.44 | 51.14 to 51.75 | -0.31 | -0.63 | no | 236 |
| 20:30:18 | 52.7 | B | 51.45 | 51.15 to 51.75 | -0.30 | -0.64 | no | 245 |
| 20:31:44 | 54.4 | B | 51.45 | 51.17 to 51.74 | -0.30 | -0.66 | no | 251 |
| 20:33:07 | 55.9 | B | 51.43 | 51.16 to 51.71 | -0.32 | -0.72 | no | 255 |
| 20:34:32 | 57.8 | B | 51.45 | 51.19 to 51.72 | -0.29 | -0.70 | no | 265 |
| 20:35:55 | 59.2 | B | 51.46 | 51.21 to 51.71 | -0.29 | -0.70 | no | 269 |
| 20:37:18 | 60.9 | B | 51.45 | 51.22 to 51.69 | -0.29 | -0.75 | no | 274 |
| 20:38:42 | 62.1 | B | 51.46 | 51.23 to 51.69 | -0.29 | -0.76 | no | 281 |
| 20:40:05 | 64.2 | B | 51.50 | 51.29 to 51.72 | -0.25 | -0.69 | no | 289 |
| 20:41:29 | 65.8 | B | 51.53 | 51.33 to 51.73 | -0.22 | -0.64 | no | 295 |
| 20:42:48 | 67.0 | B | 51.52 | 51.32 to 51.72 | -0.23 | -0.69 | no | 302 |
| 20:44:11 | 69.0 | B | 51.51 | 51.33 to 51.69 | -0.24 | -0.77 | no | 308 |
| 20:45:30 | 70.2 | B | 51.52 | 51.35 to 51.70 | -0.22 | -0.75 | no | 311 |
| 20:46:50 | 71.7 | B | 51.53 | 51.37 to 51.70 | -0.22 | -0.76 | no | 315 |
| 20:48:08 | 73.5 | B | 51.55 | 51.39 to 51.70 | -0.20 | -0.76 | no | 319 |
| 20:49:26 | 74.9 | B | 51.57 | 51.43 to 51.71 | -0.18 | -0.72 | no | 323 |
| 20:50:44 | 76.5 | B | 51.58 | 51.45 to 51.71 | -0.17 | -0.70 | no | 329 |
| 20:52:01 | 77.7 | B | 51.59 | 51.47 to 51.72 | -0.16 | -0.70 | no | 335 |
| 20:53:16 | 79.4 | B | 51.61 | 51.49 to 51.72 | -0.14 | -0.69 | no | 341 |
| 20:54:33 | 80.7 | B | 51.62 | 51.52 to 51.73 | -0.12 | -0.64 | no | 345 |
| 20:55:49 | 82.2 | B | 51.65 | 51.55 to 51.75 | -0.10 | -0.56 | no | 353 |
| 20:57:06 | 83.6 | B | 51.67 | 51.57 to 51.76 | -0.08 | -0.48 | yes | 356 |
| 20:58:22 | 84.9 | B | 51.68 | 51.60 to 51.76 | -0.07 | -0.44 | yes | 364 |
| 20:59:38 | 86.4 | B | 51.68 | 51.60 to 51.75 | -0.07 | -0.51 | yes | 371 |
| 21:00:52 | 87.9 | B | 51.70 | 51.63 to 51.76 | -0.05 | -0.37 | yes | 373 |
| 21:02:07 | 89.1 | B | 51.70 | 51.63 to 51.75 | -0.05 | -0.46 | yes | 379 |
| 21:03:24 | 90.7 | B | 51.71 | 51.65 to 51.76 | -0.04 | -0.41 | yes | 381 |
| 21:04:38 | 91.9 | B | 51.72 | 51.67 to 51.77 | -0.02 | -0.28 | yes | 385 |
| 21:05:52 | 93.4 | B | 51.74 | 51.70 to 51.77 | -0.01 | -0.16 | yes | 387 |
| 21:07:05 | 95.0 | B | 51.76 | 51.73 to 51.78 | +0.01 | +0.21 | yes | 390 |
| 21:08:16 | 95.9 | B | 51.75 | 51.73 to 51.77 | +0.00 | +0.09 | yes | 391 |
| 21:09:26 | 96.7 | B | 51.75 | 51.73 to 51.77 | +0.01 | +0.18 | yes | 393 |
| 21:10:37 | 98.5 | B | 51.77 | 51.76 to 51.77 | +0.02 | +1.53 | no | 395 |
| 21:11:45 | 99.7 | B | 51.77 | 51.77 to 51.77 | +0.02 | +7.03 | no | 397 |

## 10. Limitations and open points

- **Arrival bias**: collection is ordered by which sections arrive first. The model conditions on R1 and on size/UF/region, but does not model the *within-municipality* effect (early sections may not represent the whole municipality). The dashboard itself warns about this.
- **`lam_reg`**: stayed at 10. Decision taken: the test with 0.1 (`diag_overestimation.py`) did not correct the bias, so regional shrinkage is not the cause.
- **UFs without a mature sampled municipality** receive a UF effect shrunk to zero; their uncertainty comes only from `tau`.
- **Interval collapses at the end**: makes sense with ~100% counted, but underestimates the risk of data/publication error at the TSE.
- **Dependence on an undocumented endpoint** of `resultados.tse.jus.br`; the format may change.
- **Municipal unit without zone in R2**: the R1 base is per zone (`cenario_zona.csv`), but the municipal R2 JSON carries only the municipality total. In municipalities with many zones, such as the capital of RJ (49 zones in R1), the model does not know which zones have already arrived, so it does not correct the bias within the municipality (early sections of specific zones). This matters most precisely in the large ones, which arrive late. **RJ closing**: at 20:51 the RJ projection was 46.66% (27% counted, swing of +4.0 pp). In the last snapshot (21:11, 98.1% counted) RJ is at 46.87% in the count and 46.90% projected. That is, the final of RJ ended up **above** the 20:51 projection (+0.2 pp), not below; this measure does not indicate overestimation bias within the capital, but a slight underestimation. In the capital (mu 60011, 96.6% counted) A has 51.5% against 47.8% in the R1 h2h (+3.7 pp), so the high swing was real. The 98.1% is from the snapshot, not from the official result: redo with the TSE final.
- **Uniform allocation of pending votes across non-sampled municipalities (leading hypothesis for the bias `b`, untested)**: the expected remainder of each non-sampled municipality is `wt = V1 · rho · (1 - pst_UF)`, that is, **proportional to V1 and the same fraction for all municipalities of the UF**. In practice the small ones are already closed and what is pending is concentrated in medium and large ones. The uniform weight puts too many remaining votes in small municipalities, which have a smaller swing, and too few in large ones, which have a larger swing. The share of the remainder comes out underestimated, which is compatible with `b ≈ -0.65 pp` (§9.2). It acts through `s_uf` (the expected share of the remainder), not through the quantity `R`, which is consistent with the measurement of §9.2, item 2 (error in `R` of at most ~0.007 pp). **This is a hypothesis, not an established cause, and it has not been tested yet**; see validation in §11, item 8.
- **Abroad (`zz`)** treated as one "UF" with region `EX`.
- **Legacy cache**: `cache/*.json` and the parquet `t1_6257_c1_13_22.parquet` are not read by the current code, except `legacy_state_swing.py`. The script that generated the parquet is not in the folder.
- **Folder layout**: the scripts use paths relative to the current directory and import each other (`import projection_r2`), so they run from the project root.

## 11. Next version

1. **Write one snapshot per cycle** (`snapshots/YYYYMMDD_HHMMSS.csv`) instead of overwriting `ultimo_snapshot.csv`. It is a prerequisite for any diagnostic: today only the last snapshot exists, and the RJ projection at 20:51 (§10) only exists in the numbers reported at the time, with no file to check.
2. **Live out-of-sample check.** Keep, at each cycle, the `p_hat` of the municipalities that are still open, and compare it with their realized share when they close in later cycles, weighting by size (votes). This is an error measured out of sample **during the night**: it estimates `b` while the count is under way, so it can be added to the projection (as a correction of `s_uf`) and to the CI (so that the interval covers the bias it measures, which the bootstrap does not, §9.2 item 3). It depends on item 1.
3. **Pending vote allocation by size**: learn from the sampled municipalities an arrival curve per band of `V1` and use it instead of the uniform `pst_UF`. Directly attacks the hypothesis of §10.
4. **Non-linear size** (bands of `V1` or a spline instead of just `zc`). A `tau` floor depending on % counted was considered and dropped as unsupported: the measured CI failure is a bias that the bootstrap does not model (§9.2, item 3), and the share of the CI width that comes from the shock cannot be measured with the data kept.
5. **`rho` prior from history** instead of the fixed 0.97: use the ratio of valid votes R2/R1 observed in previous elections. On the night, the national median of `V2/V1` among the 347 closed municipalities of the last snapshot was 1.039; the fixed 0.97 only applies when fewer than 20 municipalities are closed (§5.3).
6. **Large-municipality stratum with training from `pst >= 20`** (today 50). Large ones arrive late; bringing them into the fit earlier should reduce the bias.
7. **Backtest with the real arrival order in 2018 and 2022.** Replay the cycles with the real publication order to measure bias and CI coverage by counted band, with several trajectories (the only way to estimate coverage for real).
8. **Validation with the R2 munzona file**: when the TSE publishes the R2 `munzona`, measure the real swing (R2 h2h minus R1 h2h) **by size band** and check whether it grows with `V1`, as the hypothesis of §10 requires. If it does not grow, the hypothesis falls and the bias `b` has another origin.

## 12. How to run (recipe)

```powershell
pip install -r requirements.txt

# 1) R1 base (needs the TSE zip in dados_tse_2026/)
python scenario_t1.py --loss 0.2 --thetas 0.3,0.4,0.5,0.6

# 2) Map (once; needs dados_geo/brazil-states.geojson and shapely)
python gerar_br_uf_svg.py

# 3) Test without network
python test_synthetic.py

# 4) Check the gate (29 requests)
python -u projection_r2.py --e2 6258 --A 13 --B 22 --gate-only

# 5) Run the live projection
python -u projection_r2.py --ano 2026 --e2 6258 --A 13 --B 22 --watch --poll 60

# 6) Dashboard (another terminal)
python dashboard_html.py --A "Lula" --B "Flavio" --every 30 --cen 0.478,0.497

# 7) Evaluation tables and plot from the recorded election night
python docs/error_by_cycle.py
```

For another election: change `--ano`, `--e2`, `--A`, `--B`, regenerate the R1 base with the new candidates, and check with `--discover` the election codes available in `comum/config/ele-c.json`.

## 13. Glossary and fixed identifiers

| Term | Meaning |
|---|---|
| `A1, B1, O1, V1` | valid votes in R1 for A, B, others and total |
| `A2, B2, V2` | votes counted in R2 (A, B, A+B) |
| `pst` | % of sections counted (from the TSE JSON) |
| `h2h` | head-to-head: A/(A+B), ignoring others |
| `rho` | `V2/V1` in closed municipalities |
| `R` | expected remaining votes |
| `swing` | change of A's share between R1 h2h and R2 |
| `tau` | between-UF spread used in the bootstrap shock |
| gate (portão) | UFs vs BR check before projecting |
| anchored (ancorado) | ≥ 30 mature municipalities (`pst >= 50`) in the fit |
| `apurado` | fraction of valid votes counted (`historico.csv` column) |
| `cnt` | raw count share of A (`historico.csv` column) |
| `mu` | municipality code; `_resto` = "the rest" row of a UF in the snapshot |

**Fixed identifiers.** These names are data contracts with the recorded election-night files and the TSE feed, and are kept in Portuguese on purpose:

- CSV columns: `ts, point, lo, hi, cnt, pA, apurado, n_fit, tau, status, div_br` (`historico.csv`); `uf, mu, A1, B1, A2, B2, Vhat, R, p_hat, pst` (`ultimo_snapshot.csv`); `uf, mu, zona, nm, A1, B1, O1, V1, h2h_A, share_A_t*` (`cenario_*.csv`).
- TSE JSON fields: `carg, agr, par, cand, n, vap, s.pst, hg, idg, cdabr` and the `-u.json` URL scheme.
- `status` values: `ok`, `nao_ancorado` (not anchored), `inconsistente` (inconsistent).
- Data files: `historico.csv`, `ultimo_snapshot.csv`, `cenario_municipio.csv`, `cenario_zona.csv`, `br_uf_svg.json` and the `dados_tse_2026/`, `dados_geo/`, `cache/` folders.
- Electoral terms kept as proper nouns: TSE (Superior Electoral Court), UF (state), `zona` (electoral zone), `seção` (polling section), `munzona` (TSE results file per municipality and zone).
