#!/usr/bin/env python3
"""Builds dashboard.html from historico.csv and ultimo_snapshot.csv (written by projection_r2.py).
Usage (another terminal, same folder):  python dashboard_html.py --A "Name A" --B "Name B"
Then open dashboard.html in the browser; the page reloads itself."""
import argparse, json, re, time
from pathlib import Path
import numpy as np, pandas as pd

TPL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="__REFRESH__"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Runoff projection</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1115;--card:#171a21;--tx:#e6e8eb;--mu:#8b93a1;--a:#ff5d5d;--b:#4f8cff;--ln:#262b35}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px}
h1{font-size:18px;margin:0 0 4px}.sub{color:var(--mu);font-size:12px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:14px}
.k{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:12px 14px}
.k .l{color:var(--mu);font-size:12px}.k .v{font-size:24px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.k .d{font-size:12px;color:var(--mu)}
.bar{height:10px;border-radius:5px;overflow:hidden;display:flex;margin:6px 0 14px;background:var(--ln)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:900px){.grid{grid-template-columns:1fr}}
.c{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:14px;margin-bottom:12px}
.c h2{font-size:13px;font-weight:600;margin:0 0 8px;color:var(--mu)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--ln);text-align:right}th:first-child,td:first-child{text-align:left}
th{color:var(--mu);font-weight:500;cursor:pointer}
.pos{color:var(--a)}.neg{color:var(--b)}
.lg{display:flex;align-items:center;gap:10px;justify-content:center;margin-top:8px;font-size:12px;color:var(--mu);flex-wrap:wrap}
.lg .gr{width:220px;height:10px;border-radius:5px;background:linear-gradient(90deg,rgb(79,140,255),rgb(217,220,226),rgb(255,93,93))}
.lg .sw{display:inline-block;width:12px;height:12px;border-radius:3px;background:#2a2e37;border:1px solid var(--ln);vertical-align:-2px}
.fn{color:var(--mu);font-size:12px;margin-top:8px;text-align:center}
</style></head><body><div class="wrap">
<h1>Second-round projection</h1>
<div class="sub" id="sub"></div>
<div class="kpis" id="kpis"></div>
<div class="bar" id="bar"></div>
<div class="c" id="mapcard"><h2>Swing vs R1 by UF (pp, same municipalities)</h2><div id="map"></div>
 <div class="lg"><span id="lgb"></span><span>−8 pp</span><span class="gr"></span><span>+8 pp</span><span id="lga"></span>
 <span><span class="sw"></span> insufficient data</span></div>
 <div class="fn">swing measured in municipalities already counted; the first polling sections of a municipality may still not represent it</div></div>
<div class="c"><h2>Projection (solid line, 90% CI band) vs raw count (dashed)</h2><div style="height:300px"><canvas id="ts"></canvas></div></div>
<div class="grid">
 <div class="c"><h2>Projected balance by UF (thousand votes, A minus B)</h2><div style="height:560px"><canvas id="uf"></canvas></div></div>
 <div class="c"><h2>Swing per counted unit: R1 head-to-head (x) vs R2 (y)</h2><div style="height:560px"><canvas id="sc"></canvas></div></div>
</div>
<div class="c"><h2>By UF</h2><table id="tb"></table></div>
</div>
<script>
const D=__DATA__, A=D.A, B=D.B, L=D.last;
const pct=(x,d=2)=>(100*x).toFixed(d)+'%';
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
document.getElementById('sub').textContent=`Updated ${L.ts} | units in the fit ${L.n_fit} | UF tau ${(+L.tau).toFixed(3)} | unofficial statistical estimate | UF = federative unit (state)`;
const k=[['Counted (valid votes)',pct(L.apurado,1),''],[`Count ${A}`,pct(L.cnt),`${B}: ${pct(1-L.cnt)}`],
 [`Projection ${A}`,pct(L.point),D.delta==null?'':`${D.delta>=0?'+':''}${(100*D.delta).toFixed(2)} pp vs previous cycle`],
 ['90% CI',`${pct(L.lo,1)} to ${pct(L.hi,1)}`,''],[`P(${A} wins)`,pct(L.pA,0),`P(${B} wins): ${pct(1-L.pA,0)}`]];
document.getElementById('kpis').innerHTML=k.map(([l,v,d])=>`<div class="k"><div class="l">${l}</div><div class="v">${v}</div><div class="d">${d}</div></div>`).join('');
document.getElementById('bar').innerHTML=`<div style="width:${100*L.point}%;background:var(--a)"></div><div style="flex:1;background:var(--b)"></div>`;
(function(){const M=D.map;if(!M){document.getElementById('mapcard').style.display='none';return}
 document.getElementById('lgb').textContent=`blue: ${B} gained ground`;document.getElementById('lga').textContent=`red: ${A} gained ground`;
 const lim=8,neu=[217,220,226],neg=[79,140,255],pos=[255,93,93],mix=(a,b,t)=>a.map((v,i)=>Math.round(v+(b[i]-v)*t));
 const SW=Object.fromEntries(D.sw.map(r=>[r.uf,r])),f1=v=>(v>=0?'+':'')+v.toFixed(1);
 let h=`<svg viewBox="0 0 ${M.w} ${M.h}" style="width:100%;max-width:560px;display:block;margin:0 auto" role="img">`;
 for(const [uf,g] of Object.entries(M.uf)){const r=SW[uf]||{ok:false,n:0,votes:0};
  let fill='#2a2e37',tc='#8b93a1',tip=`${uf.toUpperCase()}: insufficient data (${r.n} municipalities, ${Math.round(r.votes).toLocaleString('en-US')} votes)`;
  if(r.ok){const c=mix(neu,r.swing>=0?pos:neg,Math.min(Math.abs(r.swing),lim)/lim);fill=`rgb(${c})`;
   tc=(0.299*c[0]+0.587*c[1]+0.114*c[2])>150?'#0f1115':'#fff';
   tip=`${uf.toUpperCase()}: ${f1(r.swing)} pp | ${r.n} municipalities | ${Math.round(r.votes).toLocaleString('en-US')} votes`}
  h+=`<g><title>${tip}</title><path d="${g.d}" fill="${fill}" fill-rule="evenodd" stroke="#0f1115" stroke-width="1"/><text x="${g.x}" y="${g.y}" text-anchor="middle" dominant-baseline="middle" font-size="13" font-weight="600" fill="${tc}" pointer-events="none">${uf.toUpperCase()}</text></g>`}
 document.getElementById('map').innerHTML=h+'</svg>'})();
Chart.defaults.color=css('--mu');Chart.defaults.borderColor=css('--ln');
const H=D.hist, xs=H.map(r=>r.ts);
new Chart(ts,{type:'line',data:{labels:xs,datasets:[
 {label:'CI high',data:H.map(r=>r.hi),borderWidth:0,pointRadius:0,fill:'+1',backgroundColor:'rgba(255,93,93,.18)'},
 {label:'CI low',data:H.map(r=>r.lo),borderWidth:0,pointRadius:0,fill:false},
 {label:'Projection',data:H.map(r=>r.point),borderColor:css('--a'),borderWidth:2.5,pointRadius:2},
 {label:'Count',data:H.map(r=>r.cnt),borderColor:css('--mu'),borderDash:[5,5],borderWidth:1.5,pointRadius:0},
 {label:'50%',data:H.map(()=>.5),borderColor:css('--b'),borderWidth:1,pointRadius:0},
 ...(D.cen?[{label:'R1 scenario high',data:H.map(()=>D.cen[1]),borderColor:'rgba(255,200,80,.6)',borderDash:[2,4],borderWidth:1,pointRadius:0,fill:'+1',backgroundColor:'rgba(255,200,80,.08)'},
 {label:`R1 scenario (${pct(D.cen[0],1)} to ${pct(D.cen[1],1)})`,data:H.map(()=>D.cen[0]),borderColor:'rgba(255,200,80,.6)',borderDash:[2,4],borderWidth:1,pointRadius:0}]:[])]},
 options:{maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{filter:i=>!i.text.startsWith('CI')&&i.text!=='R1 scenario high'}},
 tooltip:{callbacks:{label:c=>`${c.dataset.label}: ${pct(c.raw)}`}}},scales:{y:{ticks:{callback:v=>pct(v,1)}}}}});
const U=[...D.uf].sort((a,b)=>b.balance-a.balance);
new Chart(document.getElementById('uf'),{type:'bar',data:{labels:U.map(r=>r.uf.toUpperCase()),datasets:[{data:U.map(r=>r.balance/1e3),
 backgroundColor:U.map(r=>r.balance>=0?css('--a'):css('--b'))}]},options:{indexAxis:'y',maintainAspectRatio:false,animation:false,
 plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>{const r=U[c.dataIndex];return `balance ${Math.round(r.balance/1e3).toLocaleString('en-US')}k | counted ${pct(r.apurado,0)} | ${A} proj ${pct(r.A_proj)}`}}}},
 scales:{y:{ticks:{autoSkip:false,font:{size:11}}}}}});
const S=D.pts;
new Chart(document.getElementById('sc'),{type:'scatter',data:{datasets:[
 {data:S.map(p=>({x:p[0],y:p[1],r:p[2],uf:p[3]})),backgroundColor:'rgba(255,93,93,.35)',pointRadius:c=>c.raw?c.raw.r:2},
 {type:'line',data:[{x:0,y:0},{x:1,y:1}],borderColor:css('--mu'),borderDash:[3,3],borderWidth:1,pointRadius:0}]},
 options:{maintainAspectRatio:false,animation:false,plugins:{legend:{display:false},
 tooltip:{callbacks:{label:c=>c.raw.uf?`${c.raw.uf.toUpperCase()} R1 ${pct(c.raw.x,1)} -> R2 ${pct(c.raw.y,1)}`:''}}},
 scales:{x:{min:0,max:1,title:{display:true,text:`${A} in R1 (vs ${B})`},ticks:{callback:v=>pct(v,0)}},
 y:{min:0,max:1,title:{display:true,text:`${A} in R2`},ticks:{callback:v=>pct(v,0)}}}}});
const cols=[['uf','UF',v=>v.toUpperCase()],['apurado','Counted',v=>pct(v,0)],['A_r1','R1 h2h',pct],['A_count','Count',pct],
 ['A_proj','Projection',pct],['balance','Balance (k)',v=>`<span class="${v>=0?'pos':'neg'}">${v>=0?'+':''}${Math.round(v/1e3).toLocaleString('en-US')}</span>`]];
let sk='apurado',sd=1;function draw(){const R=[...D.uf].sort((a,b)=>(a[sk]>b[sk]?1:-1)*sd);
 tb.innerHTML='<tr>'+cols.map(c=>`<th data-k="${c[0]}">${c[1]}</th>`).join('')+'</tr>'+R.map(r=>'<tr>'+cols.map(c=>`<td>${c[2](r[c[0]])}</td>`).join('')+'</tr>').join('');
 tb.querySelectorAll('th').forEach(t=>t.onclick=()=>{sd=sk===t.dataset.k?-sd:1;sk=t.dataset.k;draw()});}draw();
</script></body></html>"""

WAIT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="__R__">
<title>Runoff projection</title></head><body style="background:#0f1115;color:#8b93a1;font:15px system-ui;padding:40px">
Waiting for a valid projection... __MSG__</body></html>"""

def _write(html):
    tmp = Path("dashboard.tmp.html"); tmp.write_text(html, encoding="utf-8"); tmp.replace("dashboard.html")

def swing_uf(s, ufs, min_mun=3, min_votes=5000):
    """Swing per UF (pp): A2+B2-weighted mean of (A2/(A2+B2) - A1/(A1+B1)) over sampled municipalities with votes."""
    m = s[(s.mu.astype(str) != "_resto") & ((s.A2 + s.B2) > 0) & ((s.A1 + s.B1) > 0)].copy()
    m["v"] = m.A2 + m.B2
    m["d"] = (m.A2 / m.v - m.A1 / (m.A1 + m.B1)) * 100
    out = []
    for uf in ufs:
        g = m[m.uf == uf]; n, v = len(g), float(g.v.sum())
        sw = float((g.d * g.v).sum() / v) if v > 0 else None
        out.append(dict(uf=uf, swing=sw, n=n, votes=v, ok=bool(n >= min_mun and v >= min_votes)))
    return out

def build(A, B, refresh, cen=None):
    if not Path("historico.csv").exists() or not Path("ultimo_snapshot.csv").exists():
        _write(WAIT.replace("__R__", str(refresh)).replace("__MSG__", "(0 cycles recorded)"))
        return
    h = pd.read_csv("historico.csv")
    n_all = len(h)
    if "status" in h: h = h[h.status.astype(str).str.lower() != "inconsistente"]
    h = h[pd.to_numeric(h.point, errors="coerce").notna()].copy() if "point" in h else h.iloc[0:0]
    if not len(h):
        _write(WAIT.replace("__R__", str(refresh)).replace("__MSG__", f"({n_all} cycles recorded, none with a valid projection)"))
        return
    for c in ["point", "lo", "hi", "cnt", "pA", "apurado", "tau", "n_fit"]:
        if c in h: h[c] = pd.to_numeric(h[c], errors="coerce")
    s = pd.read_csv("ultimo_snapshot.csv")
    s["valid2"] = s.A2 + s.B2
    s["Ap"] = s.R * s.p_hat
    g = s.groupby("uf").agg(A2=("A2", "sum"), v2=("valid2", "sum"), Vhat=("Vhat", "sum"),
                            R=("R", "sum"), Ap=("Ap", "sum"), A1=("A1", "sum"), B1=("B1", "sum")).reset_index()
    tot = g.v2 + g.R
    uf = pd.DataFrame({"uf": g.uf, "apurado": g.v2 / g.Vhat.clip(lower=1),
                       "A_r1": g.A1 / (g.A1 + g.B1).clip(lower=1), "A_count": g.A2 / g.v2.clip(lower=1),
                       "A_proj": (g.A2 + g.Ap) / tot.clip(lower=1), "balance": 2 * (g.A2 + g.Ap) - tot})
    u = s[s.valid2 >= 30]
    if len(u) > 4000: u = u.nlargest(4000, "valid2")
    r = (np.sqrt(u.valid2) / max(np.sqrt(u.valid2).max(), 1) * 14 + 1.5)
    pts = np.column_stack([(u.A1 / (u.A1 + u.B1).clip(lower=1)).round(4), (u.A2 / u.valid2).round(4),
                           r.round(1)]).tolist()
    pts = [p + [str(x)] for p, x in zip(pts, u.uf)]
    last = h.iloc[-1].to_dict()
    mp = json.loads(Path("br_uf_svg.json").read_text(encoding="utf-8")) if Path("br_uf_svg.json").exists() else None
    data = dict(A=A, B=B, last=last, cen=cen, map=mp, sw=swing_uf(s, list(mp["uf"]) if mp else []), delta=(float(h.point.iloc[-1] - h.point.iloc[-2]) if len(h) > 1 else None),
                hist=h[["ts", "point", "lo", "hi", "cnt"]].to_dict("records"),
                uf=uf.round(5).to_dict("records"), pts=pts)
    js = re.sub(r"\b(NaN|-?Infinity)\b", "null", json.dumps(data, default=float))
    _write(TPL.replace("__DATA__", js).replace("__REFRESH__", str(refresh)))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--A", default="A", help="display name of candidate A"); ap.add_argument("--B", default="B", help="display name of candidate B")
    ap.add_argument("--every", type=int, default=30, help="seconds between rebuilds (also the page refresh interval)")
    ap.add_argument("--once", action="store_true", help="build once and exit")
    ap.add_argument("--cen", default="", help="R1 scenario band for A, e.g. 0.478,0.497")
    a = ap.parse_args()
    while True:
        try:
            cen = [float(x) for x in a.cen.split(",")] if a.cen else None
            build(a.A, a.B, a.every, cen); print(time.strftime("%H:%M:%S"), "dashboard.html updated")
        except FileNotFoundError:
            print("waiting for historico.csv / ultimo_snapshot.csv ...")
        except Exception as e:
            print("error:", e)
        if a.once: break
        time.sleep(a.every)
