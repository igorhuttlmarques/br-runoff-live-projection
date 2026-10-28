> **Unofficial statistical estimate built for study; not affiliated with the TSE. Results here are not official.**

# Second-round projection: Brazilian presidential election 2026

Live projection of the second-round (R2) result from the TSE (Superior Electoral Court) partial count, anchored on the first-round (R1) result per municipality (ridge logit regression with UF and region effects, bootstrap by UF for the interval). UF = federative unit (state).

Full documentation: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Layout

| File | Role |
|---|---|
| `scenario_t1.py` | Builds the R1 base (`cenario_municipio.csv`, `cenario_zona.csv`) from the TSE zip |
| `projection_r2.py` | Live R2 ingestion + model + bootstrap |
| `dashboard_html.py` | Builds `dashboard.html` with the real-time projection |
| `gerar_br_uf_svg.py` | Builds `br_uf_svg.json` (map by UF) |
| `legacy_state_swing.py` | First approach, swing in the UFs already counted (legacy, discarded) |
| `diag_overestimation.py`, `diag_pst.py` | Offline diagnostics |
| `test_synthetic.py` | Offline test with synthetic data |
| `cenario_municipio.csv`, `cenario_zona.csv` | R1 base per municipality / electoral zone (versioned, avoids downloading the 450 MB zip) |
| `data/election_night_2026-10-25/` | Recorded election night (2026-10-25): `historico.csv`, `ultimo_snapshot.csv`, `logs/watch.log`; original names kept |
| `docs/` | Methodology and evaluation (`error_by_cycle.py` reads the recorded folder and regenerates the tables and plot) |

Scripts use relative paths and import each other: run everything from the project root.

## Quick start

```powershell
pip install -r requirements.txt
python test_synthetic.py
python -u projection_r2.py --e2 6258 --A 13 --B 22 --gate-only
python -u projection_r2.py --e2 6258 --A 13 --B 22 --watch
python dashboard_html.py --A "Lula" --B "Flavio"
```

## Not versioned (see `.gitignore`)

`dados_tse_2026/` (450 MB zip, download from the TSE Open Data portal), `cache/`, `dados_geo/`, logs and `dashboard.html` (run output). The projector writes `historico.csv` and `ultimo_snapshot.csv` to the working directory (also not versioned); the versioned record of the 2026-10-25 count is `data/election_night_2026-10-25/`.

## Fixed identifiers

Some names are data contracts and are intentionally kept in Portuguese: CSV column names (`A1`, `B1`, `O1`, `V1`, `A2`, `B2`, `pst`, `p_hat`, `R`, `Vhat`, `status`, `div_br`, `apurado`, ...), TSE JSON fields, status values (`ok`, `nao_ancorado`, `inconsistente`) and the data file names above. See the glossary in [docs/METHODOLOGY.md](docs/METHODOLOGY.md#13-glossary-and-fixed-identifiers).
