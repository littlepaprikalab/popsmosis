-- The style a droplet's song was made from: the studio's brain writes it into the bundle (music.positive_styles) from
-- the style the learner suggested, or from the rotation's seed, and the API copies it here when the lesson is posted
-- (api/src/lib/song-style.ts), for the lesson card. Null: no song.
ALTER TABLE "answers" ADD COLUMN "songStyle" TEXT;

-- The droplets made before it, from their bundles, joined as the API joins them. One that reads as holding an email
-- address or a phone number is left out, as the API leaves it out (here more broadly: any "@").
WITH parts AS (
  SELECT a.id, e.n, regexp_replace(btrim(regexp_replace(e.value, '\s+', ' ', 'g')), '[ .]+$', '') AS part
  FROM "answers" AS a
  CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(a.bundle->'music'->'positive_styles') = 'array' THEN a.bundle->'music'->'positive_styles' END
  ) WITH ORDINALITY AS e(value, n)
), styles AS (
  SELECT id, string_agg(part, ', ' ORDER BY n) AS style FROM parts WHERE part <> '' GROUP BY id
)
UPDATE "answers" AS a SET "songStyle" = s.style
FROM styles AS s
WHERE a.id = s.id AND length(s.style) <= 1000
  AND s.style !~ '@' AND s.style !~ '[0-9]{3}[-. ]?[0-9]{3}[-. ]?[0-9]{4}';
