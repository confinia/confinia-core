-- UK reconciliation: the CHD (ons-chd, exact legal dates) becomes the temporal
-- backbone; the geometries of the UK eurostat-lau rows (identical GSS codes)
-- are brought over onto the CHD versions, then the UK LAU rows (frozen 2016
-- edition, wrong post-Brexit periods) are removed. Schema-neutral: run with
-- the desired search_path (blue/green data: SET search_path TO staging, public).

-- 1) Bring the best available LAU geometry onto each LAD version.
--    geometry_approx = true: the geometry edition does not necessarily
--    coincide with the legal period of the version.
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

-- 2) Remove the UK LAU duplicates (the CHD is now authoritative).
DELETE FROM commune_version WHERE country = 'UK' AND unit_type = 'lau';

-- 3) Checks.
SELECT 'lad versions with geometry' AS controle, count(*) FROM commune_version
WHERE source = 'ons-chd' AND geom IS NOT NULL
UNION ALL
SELECT 'lad versions without geometry', count(*) FROM commune_version
WHERE source = 'ons-chd' AND geom IS NULL
UNION ALL
SELECT 'remaining UK lau rows (expected 0)', count(*) FROM commune_version
WHERE country = 'UK' AND unit_type = 'lau'
UNION ALL
SELECT 'database total', count(*) FROM commune_version;
