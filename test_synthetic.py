"""Synthetic test of projection_r2.py: biased arrival (small municipalities and pro-B first), no network."""
import argparse, asyncio, os, re, sys, tempfile
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import projection_r2 as pt

t = pt.prep_t1(pd.read_csv(os.path.join(HERE, "cenario_municipio.csv"), dtype={"mu": str})[["uf", "mu", "A1", "B1", "O1", "V1"]])
t = t[(t.V1 > 0) & (t.A1 + t.B1 > 0)].reset_index(drop=True)
rng = np.random.default_rng(42)
ufs = sorted(t.uf.unique()); regs = sorted(t.reg.unique())
ue = dict(zip(ufs, rng.normal(0, .25, len(ufs)))); re_ = dict(zip(regs, rng.normal(0, .15, len(regs))))
eta = 0.10 + 0.9 * t.x1 - 1.0 * t.x2 + 0.05 * t.zc + t.uf.map(ue) + t.reg.map(re_) + rng.normal(0, .12, len(t))
p2 = pt.expit(eta.to_numpy())
V2 = (t.V1 * np.clip(rng.normal(.96, .02, len(t)), .85, 1.0)).round().to_numpy()
A2t = np.round(V2 * p2); B2t = V2 - A2t
TRUE = A2t.sum() / V2.sum()
z = lambda x: (x - x.mean()) / x.std()
score = z(np.log(t.V1.to_numpy())) + .8 * z(t.x1.to_numpy()) + rng.normal(0, .5, len(t))
start = pd.Series(score).rank().to_numpy() / len(t) * 0.7
IDX = {(u, m): i for i, (u, m) in enumerate(zip(t.uf, t.mu))}
UFI = t.uf.to_numpy(); V1 = t.V1.to_numpy()
STATE = dict(P=0.0, br_scale=1.0)

def js(a, b, pst):
    return {"s": {"pst": f"{pst:.2f}".replace(".", ",")}, "tpabr": "x", "cdabr": "x",
            "idg": str(int(STATE["P"] * 1000)), "hg": f"pub{STATE['P']}",
            "carg": [{"agr": [{"par": [{"cand": [{"n": "13", "vap": str(int(round(a)))}, {"n": "22", "vap": str(int(round(b)))}]}]}]}]}

async def fake_get_json(cli, url, sem, rd, tries=3):
    rd.c[200] += 1; rd.n += 1
    pm = np.clip((STATE["P"] - start) / 0.3, 0, 1)
    m = re.search(r"/dados/(\w+)/\1-c0001-e\d+-u\.json", url)
    if m:
        k = m.group(1); sel = np.ones(len(t), bool) if k == "br" else (UFI == k)
        sc = STATE["br_scale"] if k == "br" else 1.0
        return js(sc * (A2t * pm)[sel].sum(), sc * (B2t * pm)[sel].sum(), 100 * (V1 * pm)[sel].sum() / max(V1[sel].sum(), 1))
    m = re.search(r"/dados/(\w+)/\1(\d+)-c0001-e", url)
    i = IDX[(m.group(1), m.group(2))]
    return js(A2t[i] * pm[i], B2t[i] * pm[i], 100 * pm[i])
pt.get_json = fake_get_json

def run(P, br_scale=1.0):
    STATE.update(P=P, br_scale=br_scale)
    a = argparse.Namespace(ano="2026", e2="6258", cargo="1", A="13", B="22", conc=3, tau_min=None, gate_only=False)
    t1 = t[["uf", "mu", "A1", "B1", "O1", "V1", "reg", "x1", "x2", "zc"]].copy()
    a.sample = [tuple(x) for x in pt.pick_sample(t1, 400).to_numpy()]
    s = set(a.sample); t1["amo"] = [(u, m) in s for u, m in zip(t1.uf, t1.mu)]
    asyncio.run(pt.cycle(a, t1, {}, ufs))

with tempfile.TemporaryDirectory() as td:
    os.chdir(td)
    print(f"TRUTH (final) Lula = {TRUE:.2%} | R1 h2h = {t.A1.sum() / (t.A1.sum() + t.B1.sum()):.2%}")
    for P in (.10, .25, .40, .60, .80):
        pm = np.clip((P - start) / 0.3, 0, 1)
        print(f"\n--- P={P}: actually counted {(V2 * pm).sum() / V2.sum():.1%}, raw count {(A2t * pm).sum() / ((A2t + B2t) * pm).sum():.2%}")
        run(P)
    def last_row():
        return pd.read_csv("historico.csv").iloc[-1]
    print("\n--- gate, case 1: BR +5% (UFs lag BR) must be ACCEPTED")
    n0 = len(pd.read_csv("historico.csv")); run(.4, br_scale=1.05); r = last_row()
    assert len(pd.read_csv("historico.csv")) == n0 + 1 and r.status in ("ok", "nao_ancorado"), r.to_dict()
    assert r.div_br < -0.02, r.div_br
    print(f"PASS: status={r.status}, div_br={r.div_br:+.2%}")
    print("\n--- gate, case 2: UFs sum to BR +5% (BR scaled by 1/1.05) must be 'inconsistente'")
    n0 = len(pd.read_csv("historico.csv")); run(.4, br_scale=1 / 1.05); r = last_row()
    assert len(pd.read_csv("historico.csv")) == n0 + 1 and r.status == "inconsistente", r.to_dict()
    assert r.div_br > 0.02, r.div_br
    assert pd.isna(r.point), "an inconsistent cycle must not produce a projection"
    print(f"PASS: status={r.status}, div_br={r.div_br:+.2%}")
    h = pd.read_csv("historico.csv"); s = pd.read_csv("ultimo_snapshot.csv")
    print("\nhistorico.csv columns:", list(h.columns)); print(h.tail(3).to_string())
    print("snapshot columns:", list(s.columns), len(s), "rows | _resto:", (s.mu == "_resto").sum())
    print("\n=== watch: poll 0.2 s; publication changes at 1.2 s (P=.4) and 2.6 s (P=.6) ===")
    async def driver():
        STATE.update(P=.25, br_scale=1.0); await asyncio.sleep(1.2); STATE["P"] = .4
        await asyncio.sleep(1.4); STATE["P"] = .6; await asyncio.sleep(1.6)
    async def go():
        a = argparse.Namespace(ano="2026", e2="6258", cargo="1", A="13", B="22", conc=3, tau_min=None,
                               gate_only=False, poll=0.2, retry=300)
        t1 = t[["uf", "mu", "A1", "B1", "O1", "V1", "reg", "x1", "x2", "zc"]].copy()
        a.sample = [tuple(x) for x in pt.pick_sample(t1, 400).to_numpy()]
        s = set(a.sample); t1["amo"] = [(u, m) in s for u, m in zip(t1.uf, t1.mu)]
        w = asyncio.create_task(pt.watch(a, t1, {}, ufs)); await driver(); w.cancel()
    asyncio.run(go())
    print("cycles in the history:", len(pd.read_csv("historico.csv")))
    os.chdir(HERE)
