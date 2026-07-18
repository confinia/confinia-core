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
    id               bigserial PRIMARY KEY,
    code             text NOT NULL,          -- code INSEE
    nom              text NOT NULL,          -- libellé
    valid_from       date NOT NULL,          -- début de validité
    valid_to         date NOT NULL,          -- fin (9999-01-01 = toujours valide)
    parents          text[] DEFAULT '{}',    -- codes dont cette version est issue
    children         text[] DEFAULT '{}',    -- codes qui la remplacent
    geometry_vintage date,                   -- édition IGN utilisée
    geometry_approx  boolean DEFAULT false,  -- héritée d'une édition voisine
    geom             geometry(Geometry, 4326),   -- brute (requêtes spatiales)
    geom_simple      geometry(Geometry, 4326)    -- simplifiée ~50 m (web)
);
```

Index créés : `(valid_from, valid_to)`, `(code, valid_from, valid_to)`, et
GiST sur `geom` et `geom_simple`. Le chargement recrée la table (DROP+CREATE)
dans une transaction : les requêtes voient l'ancien état jusqu'au commit.

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

## Raccord géométrie IGN (`join_geometry.py`)

Joint les polygones **Admin Express COG édition** (IGN, Licence Ouverte 2.0 —
attribution « IGN — Admin Express ») au modèle temporel : appariement par code
INSEE **dans la période de validité de chaque version** (rend le réemploi de
code inoffensif), héritage du millésime le plus proche marqué
`geometry_approx: true`, sorties brute + simplifiée (~50 m). Sources SHP
(≤ 2024, reprojection Lambert-93 auto) et GeoParquet (≥ 2025). Catalogue des
éditions 2017→2026 : `data.geopf.fr/telechargement/resource/ADMIN-EXPRESS-COG`.

Sorties : `--geojson` / `--geojson-raw` (extraits, fixtures) et/ou **`--dsn`**
(chargement PostGIS streaming, brute + simplifiée par version — c'est la voie
de production, cible `load-fr` du `Makefile`). `--dsn` sans valeur lit
`$PG_DSN` ; il n'est jamais implicite, pour qu'un extrait départemental
(`join-01`) n'écrase pas la table pleine France.

Test de non-régression sur la fusion Valserhône (dept 01) : `verify_ain.py` —
voir les cibles `join-01` / `verify-01` du `Makefile` racine. Tout s'exécute en
conteneur (règles dans `DEV.md`).

## Sémantique des mouvements (acquise sur données réelles)

- Filtrer sur `TYPECOM == COM` (les fusions émettent aussi des lignes COMD/COMA).
- Ligne identité (même code+nom AV/AP) : la commune traverse l'événement — ni début ni fin.
- Même code, nom différent : renommage — fin + début, quel que soit le MOD.
- Code différent : **le MOD décide** — fin de l'AV pour {30, 31, 32, 33, 41, 50}
  (suppression, fusions côté absorbé, changements de code) ; début de l'AP pour
  {20, 21, 32, 41, 50} (création, rétablissement, commune nouvelle). Une
  création (20) ne tue pas la commune source ; une fusion (31/33) ne
  (re)démarre pas l'absorbeur.
- Un (code, nom) peut avoir **plusieurs périodes** (rétablissements — Celles 15148).
- Début inconnu (aucun événement entrant) : borné à `1943-01-01` — le fichier
  des mouvements est complet depuis 1943, ce qui rend les comptages corrects à
  toute date avec un seul millésime COG chargé. Vérifié vs INSEE publié :
  2015 : 36 617/36 658 · 2020 : 34 965/34 968 · 2025 : 34 877/34 875.

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
