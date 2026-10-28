#!/usr/bin/env python3
"""
Second-round (R2) projection from partial counts, anchored on the first round (R1) (TSE Open Data).
R2 source: resultados.tse.jus.br (-u.json, 2026). R1: cenario_municipio.csv (scenario_t1.py).
TSE = Superior Electoral Court; UF = federative unit (state); BR = national aggregate.

Usage:
  python -u projection_r2.py --ano 2026 --e2 6258 --A 13 --B 22 --loop 180
  python -u projection_r2.py --e2 6258 --A 13 --B 22 --gate-only      # only the 29 gate requests

Each cycle: gate (27 UFs + zz + BR; sum of UFs vs BR) -> municipality sample -> model.
Official count = UF aggregates. Sampled municipalities inform the model; the rest comes from the UF level.
"""
import argparse, asyncio, collections, json, math, time
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd, httpx

BASE = "https://resultados.tse.jus.br/oficial"
HDR = {"User-Agent": "Mozilla/5.0 (projection-r2)"}
REG = {**{u: "N" for u in "ac am ap pa ro rr to".split()},
       **{u: "NE" for u in "al ba ce ma pb pe pi rn se".split()},
       **{u: "CO" for u in "df go ms mt".split()},
       **{u: "SE" for u in "es mg rj sp".split()},
       **{u: "S" for u in "pr rs sc".split()}, "zz": "EX"}
HIST_COLS = ["ts", "point", "lo", "hi", "cnt", "pA", "apurado", "n_fit", "tau", "status", "div_br"]
SNAP_COLS = ["uf", "mu", "A1", "B1", "A2", "B2", "Vhat", "R", "p_hat", "pst"]

def u_agg(a, e, uf, c): return f"{BASE}/ele{a}/{e}/dados/{uf}/{uf}-c{int(c):04d}-e{int(e):06d}-u.json"
def u_v(a, e, uf, mu, c): return f"{BASE}/ele{a}/{e}/dados/{uf}/{uf}{mu}-c{int(c):04d}-e{int(e):06d}-u.json"
def nc(u): return f"{u}?nocache={int(time.time() * 1000)}"

def num(x):
    if x in (None, ""): return 0.0
    try: return float(str(x).replace(",", "."))
    except ValueError: return 0.0

# ---------------------------------------------------------------- fetch
class Round:
    """Counters for one fetch round; 3 consecutive blocks (403/429) abort."""
    def __init__(s): s.c = collections.Counter(); s.blk = 0; s.abort = False; s.n = 0; s.out = "failed"

async def get_json(cli, url, sem, rd, tries=3):
    async with sem:
        for k in range(tries):
            if rd.abort: return None
            try:
                r = await cli.get(url, timeout=20); st = r.status_code
            except httpx.HTTPError:
                rd.c["error"] += 1; await asyncio.sleep(2 * (k + 1)); continue
            rd.c[st] += 1; rd.n += 1
            if rd.n % 100 == 0: print(f"  ... {rd.n} req {dict(rd.c)}", flush=True)
            if st == 200:
                rd.blk = 0
                try: return r.json()
                except ValueError: rd.c["json"] += 1; continue
            if st in (403, 429):
                rd.blk += 1
                if rd.blk >= 3: rd.abort = True; return None
                print(f"  HTTP {st}: waiting 60 s ({rd.blk}/3)", flush=True)
                await asyncio.sleep(60); continue
            rd.blk = 0
            if st == 404: return None
            await asyncio.sleep(2 * (k + 1))
    return None

def cands(js, A, B):
    c = {str(x.get("n")): num(x.get("vap")) for cg in js.get("carg", []) for ag in cg.get("agr", [])
         for p in ag.get("par", []) for x in p.get("cand", [])}
    return c.get(A, 0.0), c.get(B, 0.0), sum(c.values())

def parse_agg(js, A, B):
    a, b, v = cands(js, A, B)
    return dict(A=a, B=b, V=v, pst=num((js.get("s") or {}).get("pst")))

# ---------------------------------------------------------------- model
def logit(p): p = np.clip(p, 1e-4, 1 - 1e-4); return np.log(p / (1 - p))
def expit(x): return 1 / (1 + np.exp(-x))

def design(d, ufs, regs):
    x1, x2 = d.x1.to_numpy(float), d.x2.to_numpy(float)
    X = np.column_stack([np.ones(len(d)), x1, x2, x1 * x2, d.zc.to_numpy(float)])
    U = (d.uf.to_numpy(dtype=object)[:, None] == np.array(ufs, dtype=object)[None, :]).astype(float)
    R = (d.reg.to_numpy(dtype=object)[:, None] == np.array(regs, dtype=object)[None, :]).astype(float)
    return np.hstack([X, U, R]), X.shape[1]

def fit(d, ufs, regs, lam_uf=25, lam_reg=10):
    """WLS ridge: logit(A2/(A2+B2)) ~ logit R1 h2h + other-candidates share + log V1 + UF and region effects (shrunk)."""
    X, k = design(d, ufs, regs)
    w = d.V2.to_numpy(float) * (d.pst.to_numpy(float) / 100).clip(0, 1) ** 2
    wm = w.mean()
    P = np.diag([1e-6] * k + [lam_uf * wm] * len(ufs) + [lam_reg * wm] * len(regs))
    XtW = X.T * w
    beta = np.linalg.solve(XtW @ X + P, XtW @ d.y.to_numpy(float))
    return beta, k

def prep_t1(t1):
    t = t1.copy()
    t["reg"] = t.uf.map(REG).fillna("EX")
    t["x1"] = logit((t.A1 + .5) / (t.A1 + t.B1 + 1))
    t["x2"] = np.where(t.V1 > 0, t.O1 / t.V1.clip(lower=1), 0)
    lv = np.log(t.V1.clip(lower=1)); t["zc"] = lv - lv.mean()
    return t

def project(t1, st, agg, tau_min=None, n_boot=300, seed=0):
    """t1: all municipalities (prep_t1) with an 'amo' (sampled) flag; st: A2,B2,V2,pst of sampled municipalities with data;
    agg: official count per UF {uf: dict(A,B,V,pst)}. Returns (result, snapshot) or (None, reason)."""
    rng = np.random.default_rng(seed)
    d = t1.merge(st, on=["uf", "mu"], how="left")
    for c in ("A2", "B2", "V2", "pst"): d[c] = d[c].fillna(0.0)
    ufs = sorted(d.uf.unique()); regs = sorted(d.reg.unique())

    # UF aggregate lagging behind its municipalities: counted_UF = max(official, sample sum)
    sm = d[d.amo].groupby("uf")[["A2", "B2"]].sum(); agg = {u: dict(v) for u, v in agg.items()}; n_adj = 0
    for u in sm.index:
        if u in agg and sm.at[u, "A2"] + sm.at[u, "B2"] > agg[u]["A"] + agg[u]["B"]:
            agg[u].update(A=float(sm.at[u, "A2"]), B=float(sm.at[u, "B2"])); n_adj += 1

    done = d[d.amo & (d.pst >= 99.9) & (d.V1 > 0)]
    g = float((done.V2 / done.V1).median()) if len(done) >= 20 else 0.97
    rho_u = done.assign(r=done.V2 / done.V1).groupby("uf").r.median() if len(done) else pd.Series(dtype=float)
    d["rho"] = d.uf.map(rho_u).fillna(g)

    # expected remainder per municipality: sampled = actual; others = V1*rho*(1 - pst_UF/100)
    f = (d.pst / 100).clip(0, 1)
    d["Vhat"] = np.where(d.pst >= 99.9, d.V2, d.V2 + (1 - f) * d.V1 * d.rho)
    d["R"] = (d.Vhat - d.V2).clip(lower=0)
    pst_uf = d.uf.map({u: agg[u]["pst"] for u in ufs if u in agg}).fillna(0.0)
    d["wt"] = np.where(d.amo, d.R, d.V1 * d.rho * (1 - pst_uf / 100).clip(0, 1))

    # UF level: official count and remainder
    U = pd.DataFrame({"A": {u: agg[u]["A"] for u in ufs}, "B": {u: agg[u]["B"] for u in ufs},
                      "pst": {u: agg[u]["pst"] for u in ufs}})
    U["V1"] = d.groupby("uf").V1.sum(); U["rho"] = d.groupby("uf").rho.first()
    U["cnt"] = U.A + U.B
    U["R"] = np.where(U.pst >= 99.9, 0.0, (1 - U.pst / 100).clip(0, 1) * U.V1 * U.rho)
    U["Vhat"] = U.cnt + U.R

    tr = d[d.amo & (d.V2 > 0) & (d.pst >= 50)]
    if len(tr) < 30: tr = d[d.amo & (d.V2 >= 30)]
    if len(tr) < 30: return None, f"too little data ({len(tr)} units in the fit)"
    tr = tr.assign(y=logit((tr.A2 + .5) / (tr.A2 + tr.B2 + 1)))
    beta, k = fit(tr, ufs, regs)
    Xall, _ = design(d, ufs, regs)
    d["p_hat"] = expit(Xall @ beta)

    iu = d.uf.map({u: i for i, u in enumerate(ufs)}).to_numpy()
    Ar, Br, Rr = U.A.reindex(ufs).to_numpy(), U.B.reindex(ufs).to_numpy(), U.R.reindex(ufs).to_numpy()
    wt = d.wt.to_numpy(float)
    def share_uf(p):
        num_ = np.bincount(iu, wt * p, len(ufs)); den = np.bincount(iu, wt, len(ufs))
        return np.where(den > 0, num_ / np.where(den > 0, den, 1), 0.5)
    tot = (Ar + Br).sum() + Rr.sum()
    def national(sh): return (Ar.sum() + (Rr * sh).sum()) / tot
    sh0 = share_uf(d.p_hat.to_numpy(float)); point = national(sh0)

    n_data = int((U.cnt > 0).sum())
    uf_tr = sorted(tr.uf.unique())
    b_uf = beta[k:k + len(ufs)]
    idx = [ufs.index(u) for u in uf_tr]
    tau = float(np.std(b_uf[idx])) if len(idx) > 1 else 0.1
    tmin = tau_min if tau_min is not None else (0.10 if n_data < 15 else 0.05)
    n_anch = int((d.amo & (d.V2 > 0) & (d.pst >= 50)).sum())     # mature municipalities in the fit
    anch = n_anch >= 30
    if not anch: tmin = 0.15                                      # fit not anchored yet
    tau = max(tau, tmin)
    rem_frac = (U.R / U.Vhat.clip(lower=1)).reindex(ufs).to_numpy()
    groups = {u: tr[tr.uf == u] for u in uf_tr}
    sims = []
    for _ in range(n_boot):
        pick = rng.choice(uf_tr, len(uf_tr), replace=True)
        try: b, _ = fit(pd.concat([groups[u] for u in pick]), ufs, regs)
        except np.linalg.LinAlgError: continue
        shock = rng.normal(0, tau * np.sqrt(rem_frac))
        sims.append(national(share_uf(expit(Xall @ b + shock[iu]))))
    sims = np.array(sims)

    # snapshot: sampled municipalities + "_resto" (the rest) per UF, closing to the official count
    s = d[d.amo].copy(); s["A1"] = s.A1.astype(float)
    snap = [s.assign(Vhat=s.V2 + s.R)[["uf", "mu", "A1", "B1", "A2", "B2", "Vhat", "R", "p_hat", "pst"]]]
    rows = []
    for i, u in enumerate(ufs):
        o, a_ = d[(d.uf == u) & ~d.amo], d[(d.uf == u) & d.amo]
        A2, B2 = max(Ar[i] - a_.A2.sum(), 0.0), max(Br[i] - a_.B2.sum(), 0.0)
        R = max(Rr[i] - a_.R.sum(), 0.0)
        w = o.wt.sum()
        p = float((o.wt * o.p_hat).sum() / w) if w > 0 else float(sh0[i])
        if A2 + B2 + R > 0 or len(o):
            rows.append(dict(uf=u, mu="_resto", A1=o.A1.sum(), B1=o.B1.sum(), A2=A2, B2=B2, Vhat=A2 + B2 + R, R=R, p_hat=p))
    snap = pd.concat(snap + [pd.DataFrame(rows)], ignore_index=True)[SNAP_COLS]
    res = dict(point=point, lo=float(np.quantile(sims, .05)), hi=float(np.quantile(sims, .95)),
               pA=float((sims > .5).mean()), cnt=float(Ar.sum() / max((Ar + Br).sum(), 1)),
               apurado=float((Ar + Br).sum() / tot), n_fit=len(tr), tau=tau, n_ufs=n_data, n_adj=n_adj, n_anch=n_anch, anch=anch)
    return res, snap

# ---------------------------------------------------------------- sample
def _alloc(m, w):
    """Split m units proportionally to w (largest remainder)."""
    q = m * w / w.sum(); k = np.floor(q).astype(int)
    for u in (q - k).sort_values(ascending=False).index[: m - int(k.sum())]: k[u] += 1
    return k

def pick_sample(t1, n, seed=7, top=100, small=150):
    """100 largest (certainty) + 150 small (V1<20k, equal probability, stratified by region) + rest stratified by UF ~ V1."""
    rng = np.random.default_rng(seed)
    keep = [t1.nlargest(min(top, n), "V1")]
    pool = t1.drop(keep[0].index)
    sp = pool[(pool.V1 > 0) & (pool.V1 < 20000)]
    if small > 0 and len(sp):
        for r, kr in _alloc(min(small, n - len(keep[0])), sp.groupby("reg").size()).items():
            g = sp[sp.reg == r]
            if kr > 0: keep.append(g.iloc[rng.choice(len(g), min(kr, len(g)), replace=False)])
        pool = pool.drop(pd.concat(keep[1:]).index)
    m = n - sum(len(x) for x in keep)
    if m > 0 and len(pool):
        for u, ku in _alloc(m, pool.groupby("uf").V1.sum()).items():
            g = pool[pool.uf == u]
            if ku > 0: keep.append(g.iloc[rng.choice(len(g), min(ku, len(g)), replace=False)])
    return pd.concat(keep)[["uf", "mu"]]

# ---------------------------------------------------------------- cycle
def log_hist(row):
    f = Path("historico.csv")
    if f.exists():                      # old history without div_br: rewrite with the new column
        h = pd.read_csv(f)
        if list(h.columns) != HIST_COLS: h.reindex(columns=HIST_COLS).to_csv(f, index=False)
    pd.DataFrame([row], columns=HIST_COLS).to_csv(f, mode="a", index=False, header=not f.exists())

async def cycle(a, t1, state, ufs):
    """One cycle. Returns the Round (rd.abort => sleep 5 min)."""
    rd = Round(); t0 = time.time(); ts = datetime.now().strftime("%H:%M:%S")
    sem = asyncio.Semaphore(a.conc)
    async with httpx.AsyncClient(headers=HDR, http2=False) as cli:
        keys = ufs + ["br"]
        res = await asyncio.gather(*[get_json(cli, nc(u_agg(a.ano, a.e2, k, a.cargo)), sem, rd) for k in keys])
        if rd.abort:
            print(f"[{ts}] gate aborted by blocking {dict(rd.c)}", flush=True); return rd
        miss = [k for k, r in zip(keys, res) if r is None]
        agg = {k: parse_agg(r, a.A, a.B) for k, r in zip(keys, res) if r is not None}
        s_uf = sum(agg[u]["V"] for u in ufs if u in agg); v_br = agg["br"]["V"] if "br" in agg else 0.0
        div = (s_uf - v_br) / v_br if v_br else None     # >0: UFs sum to more than BR; <0: UFs lagging
        dtxt = "n/a" if div is None else f"{div:+.2%}"
        print(f"[{ts}] gate: sum of UFs={s_uf:,.0f} BR={v_br:,.0f} div_br={dtxt} "
              f"pst BR={agg['br']['pst'] if 'br' in agg else float('nan'):.2f}% {dict(rd.c)}", flush=True)
        bad = [k for k in miss if k != "br"]
        if bad or (v_br and s_uf > 1.02 * v_br):
            why = f"missing files {bad}" if bad else "UFs sum to more than BR (>2%)"
            log_hist({"ts": ts, "status": "inconsistente", "div_br": div}); print(f"  INCONSISTENT cycle: {why}", flush=True)
            rd.out = "inconsistente"; return rd
        if s_uf == 0: print("  no votes yet", flush=True); rd.out = "no_votes"; return rd
        rd.out = "ok"
        if a.gate_only: return rd

        closed = {k for k, v in state.items() if v["pst"] >= 99.9}
        todo = [(u, m) for u, m in a.sample if (u, m) not in closed]
        out = await asyncio.gather(*[get_json(cli, nc(u_v(a.ano, a.e2, u, m, a.cargo)), sem, rd) for u, m in todo])
    for (u, m), js in zip(todo, out):
        if js is None: continue
        x = parse_agg(js, a.A, a.B)
        state[(u, m)] = dict(uf=u, mu=m, A2=x["A"], B2=x["B"], V2=x["A"] + x["B"], pst=x["pst"])
    st = pd.DataFrame(list(state.values())) if state else pd.DataFrame(columns=["uf", "mu", "A2", "B2", "V2", "pst"])
    r, snap = project(t1, st, {u: agg[u] for u in ufs}, a.tau_min)
    if r is None:
        print(f"  {snap} | {rd.n} req {dict(rd.c)} {time.time() - t0:.0f}s", flush=True); return rd
    official = sum(agg[u]["A"] + agg[u]["B"] for u in ufs)
    chk = (snap.A2 + snap.B2).sum()
    log_hist({"ts": ts, "point": r["point"], "lo": r["lo"], "hi": r["hi"], "cnt": r["cnt"], "pA": r["pA"],
              "apurado": r["apurado"], "n_fit": r["n_fit"], "tau": r["tau"],
              "status": "ok" if r["anch"] else "nao_ancorado", "div_br": div})
    snap.to_csv("ultimo_snapshot.csv", index=False)
    print(f"  counted ~{r['apurado']:.2%} | count A={r['cnt']:.2%} | PROJECTION A={r['point']:.2%} "
          f"[90%: {r['lo']:.2%} - {r['hi']:.2%}] | P(A wins)={r['pA']:.1%} | n_fit={r['n_fit']} tau={r['tau']:.3f} "
          f"UFs with data={r['n_ufs']} | mature(pst>=50)={r['n_anch']}{'' if r['anch'] else ' NOT ANCHORED (tau>=0.15)'} | div_br={dtxt} | UFs with sample > aggregate={r['n_adj']}\n"
          f"  snapshot A2+B2={chk:,.0f} vs official UFs={official:,.0f} (BR={v_br:,.0f}) | "
          f"{rd.n} req {dict(rd.c)} {time.time() - t0:.0f}s", flush=True)
    return rd

async def watch(a, t1, state, ufs):
    """Polls only BR every --poll s; runs the full cycle when (idg, hg) changes.
    An inconsistent/incomplete cycle repeats every --retry s (UFs may lag behind BR)."""
    last, retry_at, n = None, 0.0, 0
    while True:
        rd = Round()
        async with httpx.AsyncClient(headers=HDR, http2=False) as cli:
            js = await get_json(cli, nc(u_agg(a.ano, a.e2, "br", a.cargo)), asyncio.Semaphore(1), rd)
        ts = datetime.now().strftime("%H:%M:%S")
        if rd.abort:
            print(f"[{ts}] blocked on poll: sleeping 5 min", flush=True); await asyncio.sleep(300); continue
        key = (js.get("idg"), js.get("hg")) if js else None
        if key and (key != last or (retry_at and time.time() >= retry_at)):
            print(f"[{ts}] TSE publication hg={js.get('hg')} idg={js.get('idg')} pst={(js.get('s') or {}).get('pst')}", flush=True)
            last, retry_at, n = key, 0.0, 0
            rc = await cycle(a, t1, state, ufs)
            if rc.abort:
                print("  3 consecutive blocks: round aborted, sleeping 5 min", flush=True); await asyncio.sleep(300)
            if rc.abort or rc.out in ("inconsistente", "failed"): retry_at = time.time() + a.retry
        else:
            n += 1
            if n % 5 == 1: print(f"[{ts}] no new publication (hg={key[1] if key else '?'})", flush=True)
        await asyncio.sleep(a.poll)

# ---------------------------------------------------------------- main
def discover(ano):
    js = httpx.get(f"{BASE}/comum/config/ele-c.json", headers=HDR, timeout=20).json()
    def walk(o, depth=0):
        if isinstance(o, dict):
            if "cd" in o and ("nm" in o or "dt" in o):
                print("  " * depth + f"cd={o.get('cd')}  nm={o.get('nm')}  dt={o.get('dt')}  tp={o.get('tp')}  cdt2={o.get('cdt2')}")
            for v in o.values(): walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o: walk(v, depth)
    walk(js)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ano", default="2026", help="election year"); ap.add_argument("--e2", help="TSE election id of the second round")
    ap.add_argument("--cargo", default="1", help="TSE office code (1 = President)")
    ap.add_argument("--A", help="candidate A number"); ap.add_argument("--B", help="candidate B number")
    ap.add_argument("--t1", default="cenario_municipio.csv", help="first-round base per municipality")
    ap.add_argument("--sample-size", type=int, default=400, help="municipalities fetched per cycle")
    ap.add_argument("--conc", type=int, default=3, help="concurrent requests")
    ap.add_argument("--loop", type=int, default=180, help="seconds between cycles (without --watch)")
    ap.add_argument("--tau-min", type=float, default=None, help="floor for the UF-shock tau (overrides the default)")
    ap.add_argument("--gate-only", action="store_true", help="run only the UF-vs-BR gate requests")
    ap.add_argument("--discover", action="store_true", help="list the election codes published by the TSE")
    ap.add_argument("--watch", action="store_true", help="run a cycle only when the TSE publishes (BR timestamp changes)")
    ap.add_argument("--poll", type=int, default=60, help="seconds between BR polls (--watch)")
    ap.add_argument("--retry", type=int, default=300, help="seconds before repeating an inconsistent/failed cycle (--watch)")
    a = ap.parse_args()
    if a.discover: return discover(a.ano)
    t1 = prep_t1(pd.read_csv(a.t1, dtype={"mu": str})[["uf", "mu", "A1", "B1", "O1", "V1"]])
    a.sample = [tuple(x) for x in pick_sample(t1, a.sample_size).to_numpy()]
    t1["amo"] = [(u, m) in set(a.sample) for u, m in zip(t1.uf, t1.mu)]
    ufs = sorted(t1.uf.unique())
    print(f"{len(t1)} municipalities, {len(ufs)} UFs, sample={len(a.sample)}, ~{len(ufs) + 1 + len(a.sample)} req/cycle, conc={a.conc}", flush=True)
    state = {}
    if a.watch: return await watch(a, t1, state, ufs)
    while True:
        rd = await cycle(a, t1, state, ufs)
        if a.gate_only or not a.loop: break
        if rd.abort:
            print("  3 consecutive blocks: round aborted, sleeping 5 min", flush=True); await asyncio.sleep(300)
        else: await asyncio.sleep(a.loop)

if __name__ == "__main__":
    asyncio.run(main())
