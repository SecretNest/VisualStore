-- Pixel dimensions of the source (design §8.3 lists the resolution per member).
ALTER TABLE file ADD COLUMN width INTEGER;
ALTER TABLE file ADD COLUMN height INTEGER;
