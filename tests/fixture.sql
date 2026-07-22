-- Jeu de données MINIMAL pour les tests CI (aucune donnée réelle nécessaire) :
-- deux communes de test qui fusionnent en 2019, polygones carrés près de
-- (5.0 E, 46.0 N). Le schéma reproduit les colonnes que l'API lit vraiment.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS commune_version (
    code             text NOT NULL,
    nom              text NOT NULL,
    valid_from       date NOT NULL,
    valid_to         date NOT NULL,
    unit_type        text NOT NULL DEFAULT 'commune',
    country          text NOT NULL DEFAULT 'FR',
    source           text,
    geometry_vintage date,
    geometry_approx  boolean NOT NULL DEFAULT false,
    parents          text[],
    children         text[],
    geom             geometry(MultiPolygon, 4326),
    geom_simple      geometry(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_cv_geom ON commune_version USING gist (geom);

CREATE TABLE IF NOT EXISTS data_source (
    source         text PRIMARY KEY,
    license        text NOT NULL,
    attribution    text NOT NULL,
    commercial_use boolean NOT NULL,
    share_alike    boolean NOT NULL DEFAULT false,
    source_url     text,
    notes          text
);
INSERT INTO data_source (source, license, attribution, commercial_use) VALUES
 ('insee-cog', 'Licence Ouverte 2.0', 'INSEE, Code officiel géographique', true)
ON CONFLICT (source) DO NOTHING;

-- Testville-A (99901) et Testville-B (99902) fusionnent en Testville (99901)
-- au 2019-01-01 : de quoi produire un événement de fusion daté.
INSERT INTO commune_version
 (code, nom, valid_from, valid_to, source, geometry_vintage, parents, children, geom, geom_simple)
VALUES
 ('99901', 'Testville-A', DATE '1943-01-01', DATE '2019-01-01', 'insee-cog', DATE '2018-01-01',
  NULL, ARRAY['99901'],
  ST_Multi(ST_GeomFromText('POLYGON((5.00 46.00, 5.01 46.00, 5.01 46.01, 5.00 46.01, 5.00 46.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((5.00 46.00, 5.01 46.00, 5.01 46.01, 5.00 46.01, 5.00 46.00))', 4326))),
 ('99902', 'Testville-B', DATE '1943-01-01', DATE '2019-01-01', 'insee-cog', DATE '2018-01-01',
  NULL, ARRAY['99901'],
  ST_Multi(ST_GeomFromText('POLYGON((5.01 46.00, 5.02 46.00, 5.02 46.01, 5.01 46.01, 5.01 46.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((5.01 46.00, 5.02 46.00, 5.02 46.01, 5.01 46.01, 5.01 46.00))', 4326))),
 ('99901', 'Testville', DATE '2019-01-01', DATE '9999-01-01', 'insee-cog', DATE '2020-01-01',
  ARRAY['99901', '99902'], NULL,
  ST_Multi(ST_GeomFromText('POLYGON((5.00 46.00, 5.02 46.00, 5.02 46.01, 5.00 46.01, 5.00 46.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((5.00 46.00, 5.02 46.00, 5.02 46.01, 5.00 46.01, 5.00 46.00))', 4326)));

-- A fixture EPCI (banatic) covering the two test communes, for the EPCI
-- serving test (issue #5). Geometry = the merged Testville footprint.
INSERT INTO data_source (source, license, attribution, commercial_use) VALUES
 ('banatic', 'Licence Ouverte 2.0', 'BANATIC (Ministere de l''Interieur)', true)
ON CONFLICT (source) DO NOTHING;
INSERT INTO commune_version
 (code, nom, valid_from, valid_to, unit_type, country, source, geometry_vintage, geom, geom_simple)
VALUES
 ('200099999', 'CC de Testville', DATE '2025-01-01', DATE '9999-01-01', 'epci', 'FR', 'banatic', DATE '2025-01-01',
  ST_Multi(ST_GeomFromText('POLYGON((5.00 46.00, 5.02 46.00, 5.02 46.01, 5.00 46.01, 5.00 46.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((5.00 46.00, 5.02 46.00, 5.02 46.01, 5.00 46.01, 5.00 46.00))', 4326)));
