#!/usr/bin/env python3
"""Builds br_uf_svg.json (SVG paths per UF + label point) from dados_geo/brazil-states.geojson.
Equirectangular with cos(-15 degrees) correction, width 600, simplify 0.04. Run once."""
import json, math
from shapely.geometry import shape

W, TOL, MIN_AREA = 600, 0.04, 0.05           # degrees and degrees^2: drops tiny islands
gj = json.load(open("dados_geo/brazil-states.geojson", encoding="utf-8"))
k = math.cos(math.radians(-15))
feats = []
for f in gj["features"]:
    g = shape(f["geometry"]); polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
    big = max(polys, key=lambda p: p.area)
    keep = [p for p in polys if p.area >= MIN_AREA or p is big]
    keep = [p.simplify(TOL, preserve_topology=True) for p in keep]
    feats.append((f["properties"]["sigla"].lower(), keep, max(keep, key=lambda p: p.area).representative_point()))

minx = min(p.bounds[0] for _, ps, _ in feats for p in ps); maxx = max(p.bounds[2] for _, ps, _ in feats for p in ps)
miny = min(p.bounds[1] for _, ps, _ in feats for p in ps); maxy = max(p.bounds[3] for _, ps, _ in feats for p in ps)
s = W / ((maxx - minx) * k); H = round((maxy - miny) * s)
X = lambda lon: round((lon - minx) * k * s, 1)
Y = lambda lat: round((maxy - lat) * s, 1)

def ring(c): return "M" + "L".join(f"{X(x)} {Y(y)}" for x, y in c) + "Z"
out = {"w": W, "h": H, "uf": {}}
for uf, ps, pt in feats:
    d = "".join(ring(p.exterior.coords) + "".join(ring(i.coords) for i in p.interiors) for p in ps)
    out["uf"][uf] = {"d": d, "x": X(pt.x), "y": Y(pt.y)}
txt = json.dumps(out, separators=(",", ":"))
open("br_uf_svg.json", "w", encoding="utf-8").write(txt)
print(len(out["uf"]), "UFs |", f"{len(txt) / 1024:.0f} KB | viewBox 0 0 {W} {H}", sorted(out["uf"]))
