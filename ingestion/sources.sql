-- Registry of data sources: provenance, license, attribution, usage rights.
-- Each commune_version row references its primary source; the day paying
-- third parties exist, filtering by terms of use is a WHERE (commercial_use),
-- and the per-response attribution is generated from this registry.
-- Idempotent: replayable at will (ON CONFLICT ... DO UPDATE).

CREATE TABLE IF NOT EXISTS data_source (
    source         text PRIMARY KEY,
    license        text NOT NULL,
    attribution    text NOT NULL,
    commercial_use boolean NOT NULL,
    share_alike    boolean NOT NULL DEFAULT false,
    source_url     text,
    notes          text
);

INSERT INTO data_source (source, license, attribution, commercial_use, source_url, notes) VALUES
 ('insee-cog',         'Licence Ouverte 2.0', 'INSEE, Code officiel géographique',                                        true, 'https://www.insee.fr/fr/information/2560452', 'Modèle temporel FR : événements datés depuis 1943'),
 ('ign-admin-express', 'Licence Ouverte 2.0', 'IGN, Admin Express COG',                                                   true, 'https://geoservices.ign.fr/adminexpress',     'Géométries des versions FR (éditions 2017 à 2026)'),
 ('eurostat-nuts',     '© EuroGeographics',   '© EuroGeographics pour les frontières administratives (NUTS)',             true, 'https://ec.europa.eu/eurostat/web/gisco',     '7 versions NUTS 2003 à 2024'),
 ('eurostat-lau',      '© EuroGeographics',   '© EuroGeographics pour les frontières administratives (LAU)',              true, 'https://ec.europa.eu/eurostat/web/gisco',     'Éditions LAU 2016 à 2023'),
 ('bkg-vg250',         'dl-de/by-2-0',        '© GeoBasis-DE / BKG (dl-de/by-2-0)',                                       true, 'https://gdz.bkg.bund.de',                     'Gemeinden DE, VG250 2016 à 2025'),
 ('cbs-pdok',          'CC BY 4.0',           'CBS / Kadaster (CC BY 4.0)',                                               true, 'https://www.pdok.nl',                         'Gemeenten NL 2016 à 2026'),
 ('dbip-country-lite', 'CC BY 4.0',           'IP geolocation by DB-IP (db-ip.com), Country Lite',                        true, 'https://db-ip.com',                           'GeoIP pays d''appel (observabilité) ; jamais l''IP'),
 ('trf-gis',           'CC BY 4.0',           'Victor Gay, TRF-GIS, Mapping the Third Republic (CC BY 4.0)',              true, 'https://dataverse.harvard.edu/dataverse/TRF-GIS', 'Nomenclatures communales annuelles 1870 à 1940'),
 ('statsnz',           'CC BY 4.0',           'Stats NZ, Territorial Authority boundaries (CC BY 4.0)',                   true, 'https://maps-by-statsnz.hub.arcgis.com',      'NZ : éditions TA 2010 à 2026 (fusion Auckland incluse) ; couches iwi/traités volontairement exclues'),
 ('banatic',           'Licence Ouverte 2.0', 'BANATIC, Base nationale sur les intercommunalites (Ministere de l''Interieur)', true, 'https://www.banatic.interieur.gouv.fr', 'FR EPCI: current perimeter snapshot; historical lineage is phase 2'),
 ('ons-chd',           'OGL v3',              'Office for National Statistics, Code History Database, © Crown copyright', true, 'https://geoportal.statistics.gov.uk',         'UK : historique des codes GSS (chantier en cours)')
ON CONFLICT (source) DO UPDATE SET
    license = EXCLUDED.license, attribution = EXCLUDED.attribution,
    commercial_use = EXCLUDED.commercial_use, source_url = EXCLUDED.source_url,
    notes = EXCLUDED.notes;

ALTER TABLE commune_version ADD COLUMN IF NOT EXISTS source text REFERENCES data_source(source);

-- Backfill of the existing rows: the primary source of the TEMPORAL RECORD
-- (FR geometries come from IGN, documented in the registry).
UPDATE commune_version SET source = CASE
    WHEN unit_type LIKE 'nuts%'   THEN 'eurostat-nuts'
    WHEN unit_type = 'gemeinde'   THEN 'bkg-vg250'
    WHEN unit_type = 'gemeente'   THEN 'cbs-pdok'
    WHEN unit_type = 'lau'        THEN 'eurostat-lau'
    WHEN country = 'FR' AND unit_type = 'commune' THEN 'insee-cog'
END
WHERE source IS NULL;

CREATE INDEX IF NOT EXISTS idx_cv_source ON commune_version (source);
