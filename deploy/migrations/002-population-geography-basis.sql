-- Which geography a population figure is counted on. NULL must keep meaning
-- "we do not know": it cannot also mean "at the boundaries of the time",
-- because those are different facts and the reader deserves to know which one
-- they hold. Found by #91's spike: ISTAT publishes "ai confini dell'epoca",
-- the opposite of INSEE's harmonisation, and our report asserted the French
-- reading over any series we might hold.
ALTER TABLE public.commune_population
    ADD COLUMN IF NOT EXISTS geography_basis text;

-- Everything we hold today is INSEE, harmonised, and carries the date proving
-- it. Backfilling from that is a statement of fact, not a guess; rows without
-- a harmonisation date stay NULL, which is the honest answer for them.
UPDATE public.commune_population
   SET geography_basis = 'harmonised'
 WHERE geography_basis IS NULL AND harmonised_on IS NOT NULL;
