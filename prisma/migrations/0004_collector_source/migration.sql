-- Sumber discovery per jadwal dan per run.
--
-- JDIHN tetap menjadi bawaan supaya jadwal dan riwayat run yang sudah ada tidak berubah
-- perilakunya. PASAL_ID mencari kandidat lewat API metadata pasal.id lalu mengunduh PDF
-- resminya dari peraturan.go.id.
CREATE TYPE "CollectorSource" AS ENUM ('JDIHN', 'PASAL_ID');

ALTER TABLE "ministry_schedules"
    ADD COLUMN "source" "CollectorSource" NOT NULL DEFAULT 'JDIHN';
ALTER TABLE "collector_runs"
    ADD COLUMN "source" "CollectorSource" NOT NULL DEFAULT 'JDIHN';
