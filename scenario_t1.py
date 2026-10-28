#!/usr/bin/env python3
"""
R2 scenario per electoral zone from R1 (TSE Open Data, votacao_candidato_munzona_2026).
R1 "other" valid votes: a fraction theta goes to A (Lula), 1-theta to B (Flavio); a share --loss of
those votes is lost (abstention/blank/null).
  share_A = (A1 + theta*(1-loss)*O1) / (A1 + B1 + (1-loss)*O1)

Usage:
  python scenario_t1.py [--zip dados_tse_2026/munzona_2026.zip] [--loss 0.2] [--thetas 0.3,0.4,0.5,0.6]
Outputs: cenario_zona.csv, cenario_municipio.csv (prior/baseline for swing), summary in the terminal.
"""
import argparse, zipfile
import numpy as np, pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--zip", default="dados_tse_2026/munzona_2026.zip", help="TSE munzona zip (Open Data)")
ap.add_argument("--A", default="13", help="candidate A number"); ap.add_argument("--B", default="22", help="candidate B number")
ap.add_argument("--loss", type=float, default=0.2, help="share of R1 'other' votes lost in R2 (abstention/blank/null)")
ap.add_argument("--thetas", default="0.3,0.4,0.5,0.6", help="grid of shares of 'other' votes that go to A")
a = ap.parse_args()
thetas = [float(t) for t in a.thetas.split(",")]

d = pd.read_csv(zipfile.ZipFile(a.zip).open("votacao_candidato_munzona_2026_BR.csv"),
                sep=";", encoding="latin1", dtype=str)
d = d[(d.NR_TURNO == "1") & (d.CD_CARGO == "1")].copy()
d["v"] = d.QT_VOTOS_NOMINAIS_VALIDOS.astype(float)
d["A1"] = np.where(d.NR_CANDIDATO == a.A, d.v, 0.0)
d["B1"] = np.where(d.NR_CANDIDATO == a.B, d.v, 0.0)
d["O1"] = d.v - d.A1 - d.B1
d["uf"] = d.SG_UF.str.lower(); d["mu"] = d.CD_MUNICIPIO.str.zfill(5); d["zona"] = d.NR_ZONA.astype(int)

def scen(t, keys):
    t = t.groupby(keys, as_index=False).agg(nm=("NM_MUNICIPIO", "first"), A1=("A1", "sum"),
                                            B1=("B1", "sum"), O1=("O1", "sum"))
    k = 1 - a.loss
    t["V1"] = t.A1 + t.B1 + t.O1
    t["h2h_A"] = t.A1 / (t.A1 + t.B1).clip(lower=1)
    for th in thetas:
        t[f"share_A_t{th:g}"] = (t.A1 + th * k * t.O1) / (t.A1 + t.B1 + k * t.O1).clip(lower=1)
    return t

zona = scen(d, ["uf", "mu", "zona"]); mun = scen(d, ["uf", "mu"])
zona.to_csv("cenario_zona.csv", index=False); mun.to_csv("cenario_municipio.csv", index=False)

A, B, O, k = zona.A1.sum(), zona.B1.sum(), zona.O1.sum(), 1 - a.loss
print(f"{len(zona)} zones, {len(mun)} municipalities | R1 valid votes={zona.V1.sum():,.0f} | "
      f"A={A/zona.V1.sum():.2%} B={B/zona.V1.sum():.2%} others={O/zona.V1.sum():.2%} | loss={a.loss:.0%}")
print(f"R1 h2h (without others): A={A/(A+B):.2%}")
for th in thetas:
    print(f"  theta={th:g}  Lula={(A + th*k*O)/(A + B + k*O):.2%}")
th_emp = (0.5 * (A + B + k * O) - A) / (k * O)
print(f"tie theta (50%): {th_emp:.3f}" + ("  [OUTSIDE the grid]" if not min(thetas) <= th_emp <= max(thetas) else ""))
