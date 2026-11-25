-- Run terakhir yang memproses dokumen, supaya dashboard bisa menampilkan hasil per run/jadwal.
ALTER TABLE "source_documents" ADD COLUMN "last_run_id" UUID;
CREATE INDEX "source_documents_last_run_id_idx" ON "source_documents"("last_run_id");
CREATE INDEX "source_documents_updated_at_idx" ON "source_documents"("updated_at");
