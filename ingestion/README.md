# Ingestion COG INSEE → PostGIS (modèle temporel)

Script d'ingestion du Code Officiel Géographique de l'INSEE vers un modèle
temporel `valid_from` / `valid_to` interrogeable par date, avec les liens
parent/enfant reconstruits à partir des mouvements de communes.

## Ce que fait le script

1. Lit, pour plusieurs millésimes, deux fichiers INSEE :
   - le fichier **COMMUNE** (état des communes au 1er janvier du millésime)
   - le fichier **MVTCOMMUNE** (mouvements : fusions, créations, renommages…)
2. Reconstruit une table temporelle : une ligne = un couple `(code, nom)`
   valide sur une période `[valid_from, valid_to)`.
   La **date d'effet** (`DATE_EFF`) des mouvements est la source de vérité des
   transitions — pas la date du millésime.
3. Déduit les liens `parents` / `children` (d'où vient une commune, ce qui la remplace).
4. Charge le résultat dans **PostGIS**, ou l'exporte en **GeoJSON** si aucune base
   n'est fournie.

## Lancement

Mode démonstration (aucune dépendance, jeu de cas réels intégré) :

```bash
python3 ingest_cog.py
```

Sur de vrais fichiers INSEE téléchargés localement :

```bash
python3 ingest_cog.py \
  --millesimes 2015 2020 2025 \
  --data-dir ./insee_files \
  --geojson communes_temporel.geojson
```

Les fichiers locaux doivent être nommés `commune_YYYY.csv` et `mvtcommune_YYYY.csv`.

Vers PostGIS :

```bash
export PG_DSN="postgresql://user:pwd@localhost/chronocarte"
python3 ingest_cog.py --millesimes 2015 2020 2025 --data-dir ./insee_files
```

## Schéma produit

```sql
CREATE TABLE commune_version (
    id          bigserial PRIMARY KEY,
    code        text NOT NULL,          -- code INSEE
    nom         text NOT NULL,          -- libellé
    valid_from  date NOT NULL,          -- début de validité
    valid_to    date NOT NULL,          -- fin (9999-01-01 = toujours valide)
    parents     text[] DEFAULT '{}',    -- codes dont cette version est issue
    children    text[] DEFAULT '{}',    -- codes qui la remplacent
    geom        geometry(Geometry, 4326)
);
```

Index créés : temporel `(valid_from, valid_to)`, `code`, et spatial GiST sur `geom`.

## Les deux requêtes cibles (une fois en base)

Quelle commune contient ce point à cette date :

```sql
SELECT code, nom FROM commune_version
WHERE valid_from <= '2015-06-01' AND valid_to > '2015-06-01'
  AND ST_Contains(geom, ST_SetSRID(ST_Point(5.83, 46.11), 4326));
```

Historique complet d'un code :

```sql
SELECT nom, valid_from, valid_to, parents, children
FROM commune_version WHERE code = '01033' ORDER BY valid_from;
```

## Limites connues (à traiter ensuite)

- **Géométrie** : le COG ne contient pas les polygones. Il faut joindre le
  contour IGN Admin Express du millésime correspondant (Shapefile/GeoPackage).
  Le script accepte déjà une géométrie GeoJSON par version ; le raccord IGN
  reste à brancher.
- **Communes disparues avant le plus ancien millésime chargé** : leur date de
  début réelle est inconnue ; le script la borne à `1943-01-01` (borne basse du
  COG) et le signale. Charger un millésime plus ancien lève l'ambiguïté.
- **URLs INSEE** : instables d'un millésime à l'autre, à renseigner dans le
  dictionnaire `INSEE_SOURCES` en tête de fichier.
