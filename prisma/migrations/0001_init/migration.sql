-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "public";

-- CreateEnum
CREATE TYPE "RunTrigger" AS ENUM ('MANUAL', 'CRON');

-- CreateEnum
CREATE TYPE "RunStatus" AS ENUM ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED');

-- CreateEnum
CREATE TYPE "DocumentState" AS ENUM ('DISCOVERED', 'SKIPPED_NO_PDF', 'UPLOADED', 'DUPLICATE', 'FAILED_RETRYABLE', 'FAILED_PERMANENT', 'PARSE_FAILED', 'SOURCE_CHANGED');

-- CreateEnum
CREATE TYPE "LegalHierarchy" AS ENUM ('UUD_1945', 'TAP_MPR', 'UU_PERPU', 'PERATURAN_PEMERINTAH', 'PERPRES_KEPPRES_PERMEN', 'PERDA_PROVINSI', 'PERDA_KABUPATEN_KOTA');

-- CreateTable
CREATE TABLE "ministry_schedules" (
    "id" UUID NOT NULL,
    "ministry" TEXT NOT NULL,
    "ministry_label" TEXT NOT NULL,
    "aliases" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "keywords" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "cron_expression" TEXT NOT NULL,
    "timezone" TEXT NOT NULL DEFAULT 'Asia/Jakarta',
    "document_limit" INTEGER NOT NULL DEFAULT 10,
    "dry_run" BOOLEAN NOT NULL DEFAULT false,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "last_run_at" TIMESTAMP(3),
    "next_run_at" TIMESTAMP(3),
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ministry_schedules_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "collector_runs" (
    "id" UUID NOT NULL,
    "schedule_id" UUID,
    "ministry" TEXT NOT NULL,
    "ministry_label" TEXT NOT NULL,
    "keywords" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "trigger" "RunTrigger" NOT NULL DEFAULT 'MANUAL',
    "status" "RunStatus" NOT NULL DEFAULT 'QUEUED',
    "dry_run" BOOLEAN NOT NULL DEFAULT false,
    "document_limit" INTEGER,
    "started_at" TIMESTAMP(3),
    "finished_at" TIMESTAMP(3),
    "summary" JSONB,
    "error_message" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "collector_runs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "collector_run_logs" (
    "id" BIGSERIAL NOT NULL,
    "run_id" UUID NOT NULL,
    "ts" TIMESTAMP(3) NOT NULL,
    "level" TEXT NOT NULL,
    "logger" TEXT NOT NULL,
    "message" TEXT NOT NULL,

    CONSTRAINT "collector_run_logs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "source_documents" (
    "source_id" TEXT NOT NULL,
    "source_url" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "fingerprint" TEXT NOT NULL,
    "attachment_url" TEXT,
    "institution" TEXT,
    "legal_hierarchy" "LegalHierarchy",
    "matched_keywords" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "status" "DocumentState" NOT NULL,
    "pdf_sha256" TEXT,
    "target_document_id" TEXT,
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "error_code" TEXT,
    "error_message" TEXT,
    "first_seen_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "source_documents_pkey" PRIMARY KEY ("source_id")
);

-- CreateIndex
CREATE UNIQUE INDEX "ministry_schedules_ministry_key" ON "ministry_schedules"("ministry");

-- CreateIndex
CREATE INDEX "ministry_schedules_is_active_next_run_at_idx" ON "ministry_schedules"("is_active", "next_run_at");

-- CreateIndex
CREATE INDEX "collector_runs_status_created_at_idx" ON "collector_runs"("status", "created_at");

-- CreateIndex
CREATE INDEX "collector_runs_ministry_created_at_idx" ON "collector_runs"("ministry", "created_at");

-- CreateIndex
CREATE INDEX "collector_run_logs_run_id_id_idx" ON "collector_run_logs"("run_id", "id");

-- CreateIndex
CREATE INDEX "source_documents_status_idx" ON "source_documents"("status");

-- CreateIndex
CREATE INDEX "source_documents_institution_idx" ON "source_documents"("institution");

-- AddForeignKey
ALTER TABLE "collector_runs" ADD CONSTRAINT "collector_runs_schedule_id_fkey" FOREIGN KEY ("schedule_id") REFERENCES "ministry_schedules"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "collector_run_logs" ADD CONSTRAINT "collector_run_logs_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "collector_runs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

