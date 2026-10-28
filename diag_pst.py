#!/usr/bin/env python3
"""Swing by region/size over sampled municipalities with the snapshot's real pst (offline). Usage: diag_pst.py [min_pst ...]"""
import sys
import numpy as np, pandas as pd
import projection_r2 as pt

snap = pd.read_csv("ultimo_snapshot.csv", dtype={"mu": str})
hist = pd.read_csv("historico.csv"); last = hist[hist.status.isin(["ok", "nao_ancorado"])].iloc[-1]
c = pd.read_csv("cenario_municipio.csv", dtype={"mu": str})[["uf", "mu", "V1"]]
S = snap[snap.mu != "_resto"].merge(c, on=["uf", "mu"], how="left")
S["reg"] = S.uf.map(pt.REG).fillna("EX"); S["v2"] = S.A2 + S.B2; S["v1h"] = S.A1 + S.B1
S = S[(S.v2 > 0) & (S.v1h > 0)].copy()
S["sw"] = (S.A2 / S.v2 - S.A1 / S.v1h) * 100
S["size"] = pd.cut(S.V1, [0, 10e3, 50e3, 200e3, 1e12], labels=["<10k", "10-50k", "50-200k", ">200k"])
print(f"snapshot of cycle {last.ts} (projection {last.point:.2%} [{last.lo:.2%}-{last.hi:.2%}], status {last.status}) | "
      f"{len(S)} municipalities with votes, median pst {S.pst.median():.1f}")

def tab(g, closed):
    w = g.v2
    r = dict(n_mun=len(g), votes=int(w.sum()), weighted_swing=(g.sw * w).sum() / w.sum(), median=g.sw.median(),
             Lula_R1_h2h=g.A1.sum() / g.v1h.sum() * 100)
    if closed: r.update(turnout_h2h=w.sum() / g.v1h.sum(), turnout_V1=w.sum() / g.V1.sum())
    return pd.Series(r)

def report(th):
    G = S[S.pst >= th]; closed = th >= 99.9
    print(f"\n######## pst >= {th:g}: {len(G)} municipalities, {int(G.v2.sum()):,} votes ########")
    if not len(G): return
    print("All:", tab(G, closed).round(3).to_dict())
    order = [r for r in ["N", "NE", "CO", "SE", "S", "EX"] if r in set(G.reg)]
    print("\nBy region:"); print(pd.DataFrame({r: tab(G[G.reg == r], closed) for r in order}).T.round(3).to_string())
    print("\nBy size (V1):"); print(pd.DataFrame({p: tab(G[G["size"] == p], closed) for p in ["<10k", "10-50k", "50-200k", ">200k"] if (G["size"] == p).any()}).T.round(3).to_string())

for th in ([float(x) for x in sys.argv[1:]] or [20, 99.9]): report(th)
