#!/usr/bin/env python3
"""Offline diagnostic (no network) from ultimo_snapshot.csv + cenario_municipio.csv."""
import shutil, sys
import numpy as np, pandas as pd
import projection_r2 as pt

shutil.copy("ultimo_snapshot.csv", "diag_snapshot.csv"); shutil.copy("historico.csv", "diag_historico.csv")
snap = pd.read_csv("diag_snapshot.csv", dtype={"mu": str}); hist = pd.read_csv("diag_historico.csv")
ok = hist[hist.status == "ok"].iloc[-1]
c = pd.read_csv("cenario_municipio.csv", dtype={"mu": str})[["uf", "mu", "O1", "V1"]]
S = snap[snap.mu != "_resto"].merge(c, on=["uf", "mu"], how="left")
S["reg"] = S.uf.map(pt.REG).fillna("EX")
print(f"reference snapshot: {len(S)} sampled municipalities | last ok projection in the history: {ok.ts} point={ok.point:.4%}")

# ---------------- 1 and 2: closed municipalities
F = S[(S.R <= 0.5) & (S.A2 + S.B2 > 0) & (S.A1 + S.B1 > 0)].copy()
F["v2"] = F.A2 + F.B2; F["v1h"] = F.A1 + F.B1
F["sw"] = (F.A2 / F.v2 - F.A1 / F.v1h) * 100
def tab(g):
    w = g.v2
    return pd.Series(dict(n_mun=len(g), votes=int(w.sum()), weighted_swing=(g.sw * w).sum() / w.sum(), median=g.sw.median(),
                          turnout_h2h=w.sum() / g.v1h.sum(), turnout_V1=w.sum() / g.V1.sum(),
                          Lula_R1_h2h=g.A1.sum() / g.v1h.sum() * 100))
order = ["N", "NE", "CO", "SE", "S", "EX"]
print(f"\n== CLOSED municipalities (R=0): {len(F)} of {len(S)} sampled, {int(F.v2.sum()):,} votes")
t = F.groupby("reg").apply(tab).reindex([r for r in order if r in set(F.reg)])
print("\nBy region (swing in pp; turnout = (A2+B2)/(A1+B1) and (A2+B2)/V1):"); print(t.round(3).to_string())
F["size"] = pd.cut(F.V1, [0, 10e3, 50e3, 200e3, 1e12], labels=["<10k", "10-50k", "50-200k", ">200k"])
print("\nBy size (V1):"); print(F.groupby("size", observed=True).apply(tab).round(3).to_string())
print("\nAll closed:", tab(F).round(3).to_dict())

# ---------------- rebuild the model inputs
t1 = pt.prep_t1(pd.read_csv("cenario_municipio.csv", dtype={"mu": str})[["uf", "mu", "A1", "B1", "O1", "V1"]])
samp = set(zip(S.uf, S.mu)); t1["amo"] = [k in samp for k in zip(t1.uf, t1.mu)]
S["V2"] = S.A2 + S.B2
done = S[(S.R <= 0.5) & (S.V2 > 0) & (S.V1 > 0)]
g = float((done.V2 / done.V1).median()) if len(done) >= 20 else 0.97
rho_u = done.assign(r=done.V2 / done.V1).groupby("uf").r.median() if len(done) else pd.Series(dtype=float)
S["rho"] = S.uf.map(rho_u).fillna(g)
S["pst"] = np.where(S.R <= 0.5, np.where(S.V2 > 0, 100.0, 0.0), 100 * (1 - S.R / (S.V1 * S.rho)).clip(0, 1))
st = S[S.V2 > 0][["uf", "mu", "A2", "B2", "V2", "pst"]]
V1u = t1.groupby("uf").V1.sum(); rho_uf = pd.Series({u: rho_u.get(u, g) for u in t1.uf.unique()})
tot_u = snap.groupby("uf")[["A2", "B2", "R"]].sum()
agg = {}
for u in t1.uf.unique():
    A, B, R = (tot_u.at[u, "A2"], tot_u.at[u, "B2"], tot_u.at[u, "R"]) if u in tot_u.index else (0.0, 0.0, 0.0)
    pst_u = 100.0 if (A + B + R) > 0 and R <= 0.5 else 100 * (1 - R / (V1u[u] * rho_uf[u]))
    agg[u] = dict(A=A, B=B, V=A + B, pst=float(np.clip(pst_u, 0, 100)))
print(f"\n== Reconstruction: national rho={g:.3f} ({len(done)} closed), UF pst approximated from R")

def run(lam_reg):
    orig = pt.fit
    pt.fit = lambda d, ufs, regs, lam_uf=25, lam_reg_=lam_reg: orig(d, ufs, regs, lam_uf, lam_reg_)
    try: r, _ = pt.project(t1, st, agg)
    finally: pt.fit = orig
    return r

# ---------------- 3: coefficients of the current model (lam_reg=10)
d = t1.merge(st, on=["uf", "mu"], how="left")
for col in ("A2", "B2", "V2", "pst"): d[col] = d[col].fillna(0.0)
ufs = sorted(d.uf.unique()); regs = sorted(d.reg.unique())
tr = d[d.amo & (d.V2 > 0) & (d.pst >= 50)]
crit = "pst>=50"
if len(tr) < 30: tr = d[d.amo & (d.V2 >= 30)]; crit = "fallback V2>=30"
tr = tr.assign(y=pt.logit((tr.A2 + .5) / (tr.A2 + tr.B2 + 1)))
def coefs(lam_reg):
    beta, k = pt.fit(tr, ufs, regs, 25, lam_reg)
    return beta, k
beta, k = coefs(10)
print(f"\n== Current model (fit: {len(tr)} municipalities, {crit}; UFs in the fit: {tr.uf.nunique()})")
print("coef:", dict(zip(["intercept", "x1 (logit R1 h2h)", "x2 (share of others)", "x1*x2", "zc (centered log V1)"], np.round(beta[:k], 4))))
ue = pd.Series(beta[k:k + len(ufs)], index=ufs); re_ = pd.Series(beta[k + len(ufs):], index=regs)
print("region effect (lam 10):", re_.round(4).to_dict())
com = ue[sorted(tr.uf.unique())].sort_values()
print("UF effects (only UFs with data in the fit) - 5 lowest:", com.head(5).round(3).to_dict())
print("                                           - 5 highest:", com.tail(5).round(3).to_dict())
beta2, _ = coefs(0.1)
print("region effect (lam 0.1):", pd.Series(beta2[k + len(ufs):], index=regs).round(4).to_dict())

# ---------------- 4: reprojection
a, b = run(10), run(0.1)
print(f"\n== National reprojection (Lula), same reconstruction")
print(f"  reference recorded in the history ({ok.ts}): {ok.point:.2%} [{ok.lo:.2%} - {ok.hi:.2%}]")
print(f"  before (lam_region=10 ): {a['point']:.2%} [{a['lo']:.2%} - {a['hi']:.2%}] P(A)={a['pA']:.0%} n_fit={a['n_fit']}")
print(f"  after  (lam_region=0.1): {b['point']:.2%} [{b['lo']:.2%} - {b['hi']:.2%}] P(A)={b['pA']:.0%} n_fit={b['n_fit']}")
