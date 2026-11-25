-- Cermin katalog peraturan.go.id: satu baris per peraturan, selalu disinkronkan.
CREATE TABLE "regulations" (
    "source_id" TEXT NOT NULL,
    "catalogue" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "law_type" TEXT NOT NULL,
    "law_number" TEXT,
    "law_year" INTEGER,
    "issuer" TEXT,
    "status" TEXT,
    "relations" JSONB NOT NULL DEFAULT '[]',
    "page_url" TEXT NOT NULL,
    "pdf_url" TEXT,
    "fingerprint" TEXT,
    "text_status" TEXT NOT NULL DEFAULT 'PENDING',
    "text_attempts" INTEGER NOT NULL DEFAULT 0,
    "text_error" TEXT,
    "pdf_sha256" TEXT,
    "alignment_document_id" TEXT,
    "first_seen_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_seen_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "detail_checked_at" TIMESTAMPTZ(3),
    "changed_at" TIMESTAMPTZ(3),
    "text_synced_at" TIMESTAMPTZ(3),
    "updated_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "regulations_pkey" PRIMARY KEY ("source_id")
);
CREATE INDEX "regulations_catalogue_law_year_idx" ON "regulations"("catalogue", "law_year");
CREATE INDEX "regulations_text_status_idx" ON "regulations"("text_status");
CREATE INDEX "regulations_detail_checked_at_idx" ON "regulations"("detail_checked_at");
CREATE INDEX "regulations_status_idx" ON "regulations"("status");

-- Riwayat perubahan yang ditemukan saat cek ulang (status, relasi, PDF, judul).
CREATE TABLE "regulation_changes" (
    "id" BIGSERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "field" TEXT NOT NULL,
    "old_value" TEXT,
    "new_value" TEXT,
    "changed_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "regulation_changes_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "regulation_changes_source_id_fkey" FOREIGN KEY ("source_id") REFERENCES "regulations"("source_id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX "regulation_changes_changed_at_idx" ON "regulation_changes"("changed_at");
CREATE INDEX "regulation_changes_source_id_idx" ON "regulation_changes"("source_id");
