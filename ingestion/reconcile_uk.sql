-- Réconciliation UK : le CHD (ons-chd, dates légales exactes) devient la
-- colonne vertébrale temporelle ; les géométries des lignes eurostat-lau UK
-- (codes GSS identiques) sont rapatriées sur les versions CHD, puis les
-- lignes LAU UK (édition 2016 figée, périodes fausses post-Brexit) sont
-- retirées. Schéma-neutre : exécuter avec le search_path voulu (bleu/vert
-- données : SET search_path TO staging, public).

-- 1) Rapatrier la meilleure géométrie LAU disponible sur chaque version LAD.
--    geometry_approx = true : l'édition géométrique ne coïncide pas
--    nécessairement avec la période légale de la version.
UPDATE commune_version lad
SET geom = lau.geom,
    geom_simple = lau.geom_simple,
    geometry_vintage = lau.geometry_vintage,
    geometry_approx = true
FROM (
    SELECT DISTINCT ON (code) code, geom, geom_simple, geometry_vintage
    FROM commune_version
    WHERE country = 'UK' AND unit_type = 'lau' AND geom IS NOT NULL
    ORDER BY code, valid_to DESC, geometry_vintage DESC NULLS LAST
) lau
WHERE lad.source = 'ons-chd' AND lad.unit_type = 'lad' AND lad.code = lau.code;

-- 2) Retirer les doublons LAU UK (le CHD fait désormais autorité).
DELETE FROM commune_version WHERE country = 'UK' AND unit_type = 'lau';

-- 3) Contrôles.
SELECT 'versions lad avec géométrie' AS controle, count(*) FROM commune_version
WHERE source = 'ons-chd' AND geom IS NOT NULL
UNION ALL
SELECT 'versions lad sans géométrie', count(*) FROM commune_version
WHERE source = 'ons-chd' AND geom IS NULL
UNION ALL
SELECT 'lignes lau UK restantes (attendu 0)', count(*) FROM commune_version
WHERE country = 'UK' AND unit_type = 'lau'
UNION ALL
SELECT 'total base', count(*) FROM commune_version;
