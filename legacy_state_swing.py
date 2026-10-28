#!/usr/bin/env python3
"""
Legacy national R2 estimate: logit swing of the UFs already counted (cache/uf_*.json) applied to the
R1 h2h of each municipality (cenario_municipio.csv). No requests. Discarded approach (see docs/METHODOLOGY.md, section 8).
"""
import json, math
from pathlib import Path
import numpy as np, pandas as pd

A, B = "13", "22"
T90_4GL = 2.1318          # Student t, 4 d.f., two-sided 90%
TAU_UF = 0.10             # swing between UFs without data (logit)

def logit(p): return math.log(p / (1 - p))
def expit(x): return 1 / (1 + np.exp(-x))

mun = pd.read_csv("cenario_municipio.csv", dtype={"mu": str})
uf1 = mun.groupby("uf")[["A1", "B1"]].sum()

rows = []
for f in sorted(Path("cache").glob("uf_*.json")):
    j = json.loads(f.read_text(encoding="utf-8-sig"))
    c = {x["n"]: float(x["vap"]) for cg in j["carg"] for ag in cg["agr"] for p in ag["par"] for x in p["cand"]}
    uf = j["cdabr"].lower(); a2, b2 = c[A], c[B]
    if a2 + b2 == 0: continue          # no valid votes (e.g. zz, voters abroad): swing undefined
    h1 = uf1.loc[uf, "A1"] / (uf1.loc[uf, "A1"] + uf1.loc[uf, "B1"])
    rows.append(dict(uf=uf, hg=j["hg"], pst=float(j["s"]["pst"].replace(",", ".")), A2=a2, B2=b2,
                     cont=a2 / (a2 + b2), h2h1=h1, swing=logit(a2 / (a2 + b2)) - logit(h1)))
t = pd.DataFrame(rows); t["w"] = t.A2 + t.B2

sw = np.average(t.swing, weights=t.w)
sd = t.swing.std(ddof=1); se = sd / math.sqrt(len(t))
tot = math.sqrt(se ** 2 + TAU_UF ** 2)
hw = T90_4GL * tot

p1 = (mun.A1 + .5) / (mun.A1 + mun.B1 + 1)
def national(delta, w="V1"):
    p = expit(np.log(p1 / (1 - p1)) + delta)
    return float((p * mun[w]).sum() / mun[w].sum())

print("Caveat: the counted part of each UF is the polling sections that arrived first, not a sample of the UF.\n")
print(t.assign(pst=t.pst.map("{:.2f}%".format), cont=t.cont.map("{:.2%}".format),
               h2h1=t.h2h1.map("{:.2%}".format), swing=t.swing.map("{:+.3f}".format),
               votes=(t.A2 + t.B2).astype(int))[["uf", "hg", "pst", "votes", "cont", "h2h1", "swing"]].to_string(index=False))
print(f"\nweighted mean swing = {sw:+.3f} logit | spread between UFs = {sd:.3f} | SE(5 UFs) = {se:.3f} | "
      f"between-UF term = {TAU_UF} | total = {tot:.3f} | 90% half-width (t4df={T90_4GL}) = {hw:.3f}")
print(f"national R1 h2h (baseline) = {national(0):.2%}")
print(f"ESTIMATE Lula R2 = {national(sw):.2%}  90% CI [{national(sw - hw):.2%} ; {national(sw + hw):.2%}]")
