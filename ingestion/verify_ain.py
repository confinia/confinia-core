#!/usr/bin/env python3
"""
Test de validation Step 1 (TODO.md) : département de l'Ain autour de la fusion
Valserhône du 2019-01-01.

Vérifie, sur le GeoJSON brut produit par join_geometry.py :
  1. au 2018-06-01 : 01033 = Bellegarde-sur-Valserine ; Châtillon-en-Michaille
     (01091) et Lancrans (01205) existent ;
  2. au 2019-06-01 : 01033 = Valserhône ; les deux autres ont disparu ;
  3. géométrie : polygone de Valserhône ≈ union des polygones 2018 de ses
     3 parents (écart symétrique < 2 % de l'aire de l'union).

Usage : verify_ain.py [chemin_geojson_brut]   (défaut: /data/out/communes_01_raw.geojson)
"""
import json
import sys

from shapely.geometry import shape

FAR = "9999-01-01"
path = sys.argv[1] if len(sys.argv) > 1 else "/data/out/communes_01_raw.geojson"
feats = json.load(open(path))["features"]

def active(d):
    return {f["properties"]["code"]: f for f in feats
            if f["properties"]["valid_from"] <= d < (f["properties"]["valid_to"] or FAR)}

failures = []
def check(label, ok):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    if not ok:
        failures.append(label)

a2018, a2019 = active("2018-06-01"), active("2019-06-01")
print(f"Communes actives (01) : {len(a2018)} au 2018-06-01, {len(a2019)} au 2019-06-01")

check("2018-06-01 : 01033 est Bellegarde-sur-Valserine",
      a2018.get("01033", {}).get("properties", {}).get("nom") == "Bellegarde-sur-Valserine")
check("2018-06-01 : 01091 Châtillon-en-Michaille présente", "01091" in a2018)
check("2018-06-01 : 01205 Lancrans présente", "01205" in a2018)
check("2019-06-01 : 01033 est Valserhône",
      a2019.get("01033", {}).get("properties", {}).get("nom") == "Valserhône")
check("2019-06-01 : 01091 disparue", "01091" not in a2019)
check("2019-06-01 : 01205 disparue", "01205" not in a2019)

vals = a2019.get("01033")
parents = vals["properties"]["parents"] if vals else []
check("parents de Valserhône = {01033, 01091, 01205}", set(parents) == {"01033", "01091", "01205"})

if vals and vals["geometry"] and all(a2018.get(c, {}).get("geometry") for c in ("01033", "01091", "01205")):
    gv = shape(vals["geometry"])
    union = shape(a2018["01033"]["geometry"])
    for c in ("01091", "01205"):
        union = union.union(shape(a2018[c]["geometry"]))
    ecart = gv.symmetric_difference(union).area / union.area
    print(f"  écart symétrique Valserhône vs union des 3 parents : {ecart:.2%}")
    check("géométrie : Valserhône ≈ union des 3 parents (< 2 %)", ecart < 0.02)
else:
    check("géométries présentes pour le test d'union", False)

for code, name, vintage in (("01033", "Bellegarde-sur-Valserine", "2018-01-01"),):
    b = next((f for f in feats if f["properties"]["code"] == code
              and f["properties"]["nom"] == name), None)
    check(f"{name} : géométrie du millésime {vintage}, non approx",
          bool(b) and b["properties"]["geometry_vintage"] == vintage
          and not b["properties"]["geometry_approx"])

print()
if failures:
    sys.exit(f"{len(failures)} échec(s).")
print("Tous les contrôles passent.")
