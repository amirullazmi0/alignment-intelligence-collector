-- Sumber ketiga: katalog resmi peraturan.go.id, disusuri per jenis peraturan.
ALTER TYPE "CollectorSource" ADD VALUE IF NOT EXISTS 'PERATURAN_GO_ID';

-- Checkpoint penyusuran katalog per sumber dan jenis.
CREATE TABLE "catalog_checkpoints" (
    "source" TEXT NOT NULL,
    "catalogue" TEXT NOT NULL,
    "next_page" INTEGER NOT NULL DEFAULT 1,
    "updated_at" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "catalog_checkpoints_pkey" PRIMARY KEY ("source", "catalogue")
);
