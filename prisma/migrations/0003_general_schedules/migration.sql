-- Satu kementerian boleh punya lebih dari satu jadwal.
--
-- Batasan unik sebelumnya memaksa satu jadwal per kementerian, sehingga tidak mungkin
-- menjadwalkan kementerian yang sama dengan keyword atau frekuensi berbeda.
DROP INDEX IF EXISTS "ministry_schedules_ministry_key";

-- Jadwal general tidak terikat kementerian mana pun: cakupannya seluruh JDIHN dan
-- yang membatasi adalah keyword yang diisi pengguna. NULL dipakai untuk menandainya
-- supaya tidak perlu nilai sentinel yang bisa bertabrakan dengan nama kementerian.
ALTER TABLE "ministry_schedules" ALTER COLUMN "ministry" DROP NOT NULL;
ALTER TABLE "collector_runs" ALTER COLUMN "ministry" DROP NOT NULL;

-- Menggantikan indeks unik yang baru dibuang; pencarian per kementerian tetap cepat.
CREATE INDEX IF NOT EXISTS "ministry_schedules_ministry_idx" ON "ministry_schedules"("ministry");
