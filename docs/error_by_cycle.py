#!/usr/bin/env python3
"""Projection error per cycle vs. a proxy of the final result (raw count of the last ok cycle), from historico.csv.
Usage (from the project root):  python docs/error_by_cycle.py [data_dir]
Reads historico.csv from data_dir (default: data/election_night_2026-10-25, the recorded count).
Outputs: docs/error_by_cycle.png, docs/error_by_cycle.md (table and summary to paste into METHODOLOGY.md).
Replace OFFICIAL_FINAL with the TSE (Superior Electoral Court) result once available; None uses the proxy."""
import sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

OFFICIAL_FINAL = None
NEW_SAMPLE_SINCE = "19:39:19"      # first cycle with n_fit reset (new sample design)

DATA_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "data/election_night_2026-10-25")
h = pd.read_csv(DATA_DIR / "historico.csv")
o = h[h.status == "ok"].copy()
fin = OFFICIAL_FINAL if OFFICIAL_FINAL is not None else float(o.cnt.iloc[-1])
o["err"] = (o.point - fin) * 100
o["b"] = o.err / (1 - o.apurado).clip(lower=1e-3)   # implied bias in the share of the remainder (pp)
o["cob"] = (o.lo <= fin) & (fin <= o.hi)
o["sample"] = ["B" if t >= NEW_SAMPLE_SINCE else "A" for t in o.ts]

pc = lambda x, d=2: f"{100 * x:.{d}f}"
L = ["| Time | Counted % | Sample | Projection A % | 90% CI | Error (pp) | b (pp) | CI covered? | n_fit |", "|---|---|---|---|---|---|---|---|---|"]
for r in o.itertuples():
    L.append(f"| {r.ts} | {pc(r.apurado, 1)} | {r.sample} | {pc(r.point)} | {pc(r.lo)} to {pc(r.hi)} | "
             f"{r.err:+.2f} | {r.b:+.2f}" + f" | {'yes' if r.cob else 'no'} | {int(r.n_fit)} |")
tab = "\n".join(L)

bands = [(0, .05), (.05, .20), (.20, .60), (.60, .90), (.90, 1.01)]
S = ["| Counted band | Cycles | Mean error (pp) | Error min to max (pp) | Mean b (pp) | CI covered |", "|---|---|---|---|---|---|"]
for a, b in bands:
    g = o[(o.apurado >= a) & (o.apurado < b)]
    if len(g):
        S.append(f"| {a:.0%} to {min(b, 1):.0%} | {len(g)} | {g.err.mean():+.2f} | {g.err.min():+.2f} to {g.err.max():+.2f} | {g.b.mean():+.2f} | "
                 f"{int(g.cob.sum())}/{len(g)} |")
summary = "\n".join(S)
cov_tot = f"{int(o.cob.sum())}/{len(o)}"
Path("docs/error_by_cycle.md").write_text(
    f"Reference final: {pc(fin, 4)}% (ok cycles = {len(o)}; {cov_tot} cycles with the CI covering the final: a single autocorrelated trajectory, not a coverage rate)\n\n{summary}\n\n{tab}\n", encoding="utf-8")

fig, (ax, bx) = plt.subplots(2, 1, figsize=(9, 8), sharex=True, gridspec_kw=dict(height_ratios=[3, 2]))
x = o.apurado * 100
ax.fill_between(x, (o.lo - fin) * 100, (o.hi - fin) * 100, color="#4f8cff", alpha=.2, label="90% CI (relative to the final)")
ax.axhline(0, color="#888", lw=1)
for k, c in (("A", "#ff5d5d"), ("B", "#c0392b")):
    g = o[o['sample'] == k]
    ax.plot(g.apurado * 100, g.err, "o-", ms=3, lw=1.2, color=c, label=f"Projection error (sample {k})")
ax.axvspan(20, 60, color="#f1c40f", alpha=.08)
ax.set_ylabel("projection minus final (pp)")
ax.set_title(f"Projection error per cycle (reference final {pc(fin)}%)")
ax.set_ylim(-2.2, 1.5); ax.legend(loc="lower right", fontsize=8); ax.grid(alpha=.25)
m = o[o.apurado >= .20]
bx.axhline(0, color="#888", lw=1); bx.axhline(m.b.median(), color="#888", ls="--", lw=1, label=f"median b from 20% counted: {m.b.median():+.2f} pp")
for k, c in (("A", "#ff5d5d"), ("B", "#c0392b")):
    g = o[(o['sample'] == k) & (o.apurado >= .05)]
    bx.plot(g.apurado * 100, g.b, "o-", ms=3, lw=1.2, color=c)
bx.axvspan(20, 60, color="#f1c40f", alpha=.08); bx.set_ylim(-1.2, 1.2)
bx.set_xlabel("% counted"); bx.set_ylabel("b = error / (1 - counted)  (pp)"); bx.legend(loc="lower right", fontsize=8); bx.grid(alpha=.25)
fig.tight_layout(); fig.savefig("docs/error_by_cycle.png", dpi=130)
print(summary, "\ncoverage:", cov_tot, "\nfinal:", fin)
