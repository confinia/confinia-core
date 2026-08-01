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

-- Harmonised census series (issue #88). Mirrors the real INSEE shape: figures
-- exist ONLY for the surviving commune (99901). 99902 died in 2019, so it has
-- no row — exactly like a disappeared commune in the real file, which is what
-- lets us test the successor routing.
INSERT INTO data_source (source, license, attribution, commercial_use) VALUES
 ('insee-pop', 'Licence Ouverte 2.0', 'INSEE, Recensement de la population', true)
ON CONFLICT (source) DO NOTHING;

CREATE TABLE IF NOT EXISTS commune_population (
    country       text NOT NULL DEFAULT 'FR',
    code          text NOT NULL,
    census_year   int  NOT NULL,
    population    int  NOT NULL,
    source        text NOT NULL,
    harmonised_on date,
    PRIMARY KEY (country, code, census_year)
);
INSERT INTO commune_population (country, code, census_year, population, source, harmonised_on)
VALUES ('FR','99901',1876,1520,'insee-pop',DATE '2025-01-01'),
       ('FR','99901',1936, 893,'insee-pop',DATE '2025-01-01'),
       ('FR','99901',1990, 618,'insee-pop',DATE '2025-01-01'),
       ('FR','99901',2023, 717,'insee-pop',DATE '2025-01-01')
ON CONFLICT (country, code, census_year) DO NOTHING;

-- A SPLIT, for the population weighting (issue #94). Splitville (99910) splits
-- in 2000 into 99911 and 99912. The two halves have EQUAL area but UNEQUAL
-- population (300 / 700), so area weighting gives 0.5/0.5 while population
-- weighting gives 0.3/0.7: the test can tell the two methods apart.
INSERT INTO commune_version
 (code, nom, valid_from, valid_to, source, geometry_vintage, parents, children, geom, geom_simple)
VALUES
 ('99910', 'Splitville', DATE '1943-01-01', DATE '2000-01-01', 'insee-cog', DATE '1999-01-01',
  NULL, ARRAY['99911','99912'],
  ST_Multi(ST_GeomFromText('POLYGON((6.00 47.00, 6.02 47.00, 6.02 47.01, 6.00 47.01, 6.00 47.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((6.00 47.00, 6.02 47.00, 6.02 47.01, 6.00 47.01, 6.00 47.00))', 4326))),
 ('99911', 'Splitville-Ouest', DATE '2000-01-01', DATE '9999-01-01', 'insee-cog', DATE '2020-01-01',
  ARRAY['99910'], NULL,
  ST_Multi(ST_GeomFromText('POLYGON((6.00 47.00, 6.01 47.00, 6.01 47.01, 6.00 47.01, 6.00 47.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((6.00 47.00, 6.01 47.00, 6.01 47.01, 6.00 47.01, 6.00 47.00))', 4326))),
 ('99912', 'Splitville-Est', DATE '2000-01-01', DATE '9999-01-01', 'insee-cog', DATE '2020-01-01',
  ARRAY['99910'], NULL,
  ST_Multi(ST_GeomFromText('POLYGON((6.01 47.00, 6.02 47.00, 6.02 47.01, 6.01 47.01, 6.01 47.00))', 4326)),
  ST_Multi(ST_GeomFromText('POLYGON((6.01 47.00, 6.02 47.00, 6.02 47.01, 6.01 47.01, 6.01 47.00))', 4326)));

-- First census AFTER the 2000 split: this is the year the method must pick.
INSERT INTO commune_population (country, code, census_year, population, source, harmonised_on)
VALUES ('FR','99911',2006,300,'insee-pop',DATE '2025-01-01'),
       ('FR','99912',2006,700,'insee-pop',DATE '2025-01-01'),
       ('FR','99911',2023,320,'insee-pop',DATE '2025-01-01'),
       ('FR','99912',2023,780,'insee-pop',DATE '2025-01-01')
ON CONFLICT (country, code, census_year) DO NOTHING;
