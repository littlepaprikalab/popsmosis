-- Tags that name only the video's format, which the studio added to every video until Nov 3 ("#studysong",
-- "#studytok", "#explained", "#examprep"), taken off the droplets that carry them. On every droplet, a search for one
-- finds none in particular; the API now drops them as it stores a bundle's tags (api/src/lib/tags.ts, NO_TAGS), and
-- these are the same words. Each video's other tags keep their order. The bundle keeps what the studio wrote.
UPDATE "answers"
SET "songTags" = ARRAY(
      SELECT t FROM unnest("songTags") WITH ORDINALITY AS u(t, n)
      WHERE t <> ALL (ARRAY['studysong', 'studytok', 'explained', 'examprep', 'edutok', 'learnontiktok', 'studywithme'])
      ORDER BY n),
    "lessonTags" = ARRAY(
      SELECT t FROM unnest("lessonTags") WITH ORDINALITY AS u(t, n)
      WHERE t <> ALL (ARRAY['studysong', 'studytok', 'explained', 'examprep', 'edutok', 'learnontiktok', 'studywithme'])
      ORDER BY n)
WHERE "songTags" && ARRAY['studysong', 'studytok', 'explained', 'examprep', 'edutok', 'learnontiktok', 'studywithme']::text[]
   OR "lessonTags" && ARRAY['studysong', 'studytok', 'explained', 'examprep', 'edutok', 'learnontiktok', 'studywithme']::text[];
