-- Migration: Structured Data Support
-- Created: 2026-01-29
-- Description: Extends ingestion_files to support structured data documents (like Notion/Coda databases)
-- 
-- This migration adds:
-- 1. doc_type discriminator column to distinguish file vs data documents
-- 2. data_schema column for optional schema definitions
-- 3. data_content column for storing structured records (JSONB array)
-- 4. data_indexes column for query optimization hints
-- 5. data_version column for optimistic locking
-- 6. data_document_cache table for tracking Redis-cached documents
-- 7. GIN indexes for fast JSONB queries
--
-- Existing RLS policies automatically apply to data documents since they're in ingestion_files

BEGIN;

-- ============================================================================
-- EXTEND INGESTION_FILES TABLE FOR STRUCTURED DATA
-- ============================================================================

-- Add doc_type discriminator: 'file' for traditional files, 'data' for structured data
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS doc_type VARCHAR(20) DEFAULT 'file';

-- Add constraint for valid doc_type values
ALTER TABLE ingestion_files DROP CONSTRAINT IF EXISTS check_doc_type;
ALTER TABLE ingestion_files ADD CONSTRAINT check_doc_type 
    CHECK (doc_type IN ('file', 'data'));

-- Schema definition for data documents (optional - allows schemaless)
-- Format: { "fields": { "name": { "type": "string", "required": true }, ... }, "indexes": [...], "embedFields": [...] }
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS data_schema JSONB;

-- Actual structured data content (array of records)
-- Format: [ { "id": "uuid", "name": "...", "status": "...", ... }, ... ]
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS data_content JSONB DEFAULT '[]'::jsonb;

-- Index definitions for query optimization (computed from schema or explicit)
-- Format: { "fields": ["status", "priority"], "compound": [["status", "created_at"]] }
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS data_indexes JSONB;

-- Version for optimistic locking during concurrent updates
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS data_version INTEGER DEFAULT 1;

-- Record count for quick access without scanning data_content
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS data_record_count INTEGER DEFAULT 0;

-- Last modified timestamp for cache invalidation
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS data_modified_at TIMESTAMP;

COMMENT ON COLUMN ingestion_files.doc_type IS 'Document type: file (uploaded files) or data (structured data documents)';
COMMENT ON COLUMN ingestion_files.data_schema IS 'Optional JSON schema definition for data documents';
COMMENT ON COLUMN ingestion_files.data_content IS 'JSONB array of records for data documents';
COMMENT ON COLUMN ingestion_files.data_indexes IS 'Index definitions for optimizing queries on data documents';
COMMENT ON COLUMN ingestion_files.data_version IS 'Version number for optimistic locking';
COMMENT ON COLUMN ingestion_files.data_record_count IS 'Cached count of records in data_content';
COMMENT ON COLUMN ingestion_files.data_modified_at IS 'Timestamp of last data modification';

-- ============================================================================
-- CREATE INDEXES FOR JSONB QUERIES
-- ============================================================================

-- Index on doc_type for filtering
CREATE INDEX IF NOT EXISTS idx_ingestion_files_doc_type 
    ON ingestion_files(doc_type);

-- GIN index on data_content for containment queries (@>, ?, ?|, ?&)
-- Only for data documents to save space
CREATE INDEX IF NOT EXISTS idx_data_content_gin 
    ON ingestion_files USING GIN (data_content jsonb_path_ops)
    WHERE doc_type = 'data';

-- GIN index on data_schema for schema queries
CREATE INDEX IF NOT EXISTS idx_data_schema_gin 
    ON ingestion_files USING GIN (data_schema)
    WHERE doc_type = 'data' AND data_schema IS NOT NULL;

-- ============================================================================
-- DATA DOCUMENT CACHE TRACKING TABLE
-- ============================================================================

-- Track documents that are actively cached in Redis for fast access
CREATE TABLE IF NOT EXISTS data_document_cache (
    document_id UUID PRIMARY KEY REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
    redis_key VARCHAR(255) NOT NULL,
    cached_at TIMESTAMP DEFAULT NOW(),
    last_accessed TIMESTAMP DEFAULT NOW(),
    access_count INTEGER DEFAULT 0,
    dirty BOOLEAN DEFAULT FALSE,
    dirty_since TIMESTAMP,
    flush_scheduled_at TIMESTAMP,
    cache_size_bytes INTEGER,
    CONSTRAINT unique_redis_key UNIQUE(redis_key)
);

CREATE INDEX IF NOT EXISTS idx_data_cache_dirty ON data_document_cache(dirty) WHERE dirty = TRUE;
CREATE INDEX IF NOT EXISTS idx_data_cache_last_accessed ON data_document_cache(last_accessed);
CREATE INDEX IF NOT EXISTS idx_data_cache_flush ON data_document_cache(flush_scheduled_at) WHERE flush_scheduled_at IS NOT NULL;

COMMENT ON TABLE data_document_cache IS 'Tracks data documents actively cached in Redis for performance';
COMMENT ON COLUMN data_document_cache.redis_key IS 'Redis key prefix for this document';
COMMENT ON COLUMN data_document_cache.dirty IS 'Whether cache has unsaved changes';
COMMENT ON COLUMN data_document_cache.dirty_since IS 'When the cache first became dirty (for max dirty duration)';
COMMENT ON COLUMN data_document_cache.flush_scheduled_at IS 'When a flush operation is scheduled';

-- Enable RLS on cache table (inherits access from document)
ALTER TABLE data_document_cache ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Can see cache entry if can see the document
CREATE POLICY data_cache_select ON data_document_cache
    FOR SELECT
    USING (
        document_id IN (SELECT file_id FROM ingestion_files)
    );

-- RLS Policy: System can insert/update/delete cache entries
CREATE POLICY data_cache_modify ON data_document_cache
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- DATA DOCUMENT RECORD HISTORY (OPTIONAL - FOR AUDIT/UNDO)
-- ============================================================================

-- Track individual record changes for undo/audit functionality
CREATE TABLE IF NOT EXISTS data_record_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
    record_id VARCHAR(255) NOT NULL,
    operation VARCHAR(20) NOT NULL CHECK (operation IN ('insert', 'update', 'delete')),
    old_data JSONB,
    new_data JSONB,
    changed_by UUID,
    changed_at TIMESTAMP DEFAULT NOW(),
    batch_id UUID -- For grouping multiple changes in one operation
);

CREATE INDEX IF NOT EXISTS idx_record_history_document ON data_record_history(document_id);
CREATE INDEX IF NOT EXISTS idx_record_history_record ON data_record_history(document_id, record_id);
CREATE INDEX IF NOT EXISTS idx_record_history_time ON data_record_history(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_record_history_batch ON data_record_history(batch_id) WHERE batch_id IS NOT NULL;

COMMENT ON TABLE data_record_history IS 'Audit log of record changes in data documents';

-- Enable RLS on history table
ALTER TABLE data_record_history ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Can see history if can see the document
CREATE POLICY record_history_select ON data_record_history
    FOR SELECT
    USING (
        document_id IN (SELECT file_id FROM ingestion_files)
    );

-- RLS Policy: System can insert history entries
CREATE POLICY record_history_insert ON data_record_history
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to generate unique record IDs
CREATE OR REPLACE FUNCTION generate_record_id()
RETURNS VARCHAR(255) AS $$
BEGIN
    RETURN gen_random_uuid()::text;
END;
$$ LANGUAGE plpgsql;

-- Function to validate data against schema (if schema exists)
CREATE OR REPLACE FUNCTION validate_data_record(
    p_schema JSONB,
    p_record JSONB
) RETURNS BOOLEAN AS $$
DECLARE
    v_field_name TEXT;
    v_field_def JSONB;
    v_field_value JSONB;
    v_field_type TEXT;
    v_required BOOLEAN;
BEGIN
    -- If no schema, all records are valid
    IF p_schema IS NULL OR p_schema->'fields' IS NULL THEN
        RETURN TRUE;
    END IF;
    
    -- Check each field in schema
    FOR v_field_name, v_field_def IN SELECT * FROM jsonb_each(p_schema->'fields')
    LOOP
        v_field_value := p_record->v_field_name;
        v_field_type := v_field_def->>'type';
        v_required := COALESCE((v_field_def->>'required')::boolean, FALSE);
        
        -- Check required fields
        IF v_required AND (v_field_value IS NULL OR v_field_value = 'null'::jsonb) THEN
            RAISE EXCEPTION 'Required field % is missing', v_field_name;
        END IF;
        
        -- Skip validation if field is null and not required
        IF v_field_value IS NULL OR v_field_value = 'null'::jsonb THEN
            CONTINUE;
        END IF;
        
        -- Type validation
        CASE v_field_type
            WHEN 'string' THEN
                IF jsonb_typeof(v_field_value) != 'string' THEN
                    RAISE EXCEPTION 'Field % must be a string', v_field_name;
                END IF;
            WHEN 'integer' THEN
                IF jsonb_typeof(v_field_value) != 'number' OR 
                   v_field_value::text ~ '\.' THEN
                    RAISE EXCEPTION 'Field % must be an integer', v_field_name;
                END IF;
            WHEN 'number' THEN
                IF jsonb_typeof(v_field_value) != 'number' THEN
                    RAISE EXCEPTION 'Field % must be a number', v_field_name;
                END IF;
            WHEN 'boolean' THEN
                IF jsonb_typeof(v_field_value) != 'boolean' THEN
                    RAISE EXCEPTION 'Field % must be a boolean', v_field_name;
                END IF;
            WHEN 'array' THEN
                IF jsonb_typeof(v_field_value) != 'array' THEN
                    RAISE EXCEPTION 'Field % must be an array', v_field_name;
                END IF;
            WHEN 'object' THEN
                IF jsonb_typeof(v_field_value) != 'object' THEN
                    RAISE EXCEPTION 'Field % must be an object', v_field_name;
                END IF;
            WHEN 'enum' THEN
                IF NOT (v_field_value IN (SELECT jsonb_array_elements(v_field_def->'values'))) THEN
                    RAISE EXCEPTION 'Field % must be one of: %', v_field_name, v_field_def->'values';
                END IF;
            ELSE
                -- Unknown type, skip validation
                NULL;
        END CASE;
    END LOOP;
    
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Function to update record count after data changes
CREATE OR REPLACE FUNCTION update_data_record_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.doc_type = 'data' THEN
        NEW.data_record_count := jsonb_array_length(COALESCE(NEW.data_content, '[]'::jsonb));
        NEW.data_modified_at := NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update record count
DROP TRIGGER IF EXISTS trg_update_data_record_count ON ingestion_files;
CREATE TRIGGER trg_update_data_record_count
    BEFORE UPDATE OF data_content ON ingestion_files
    FOR EACH ROW
    WHEN (NEW.doc_type = 'data')
    EXECUTE FUNCTION update_data_record_count();

-- ============================================================================
-- GRANTS
-- ============================================================================

GRANT SELECT, INSERT, UPDATE, DELETE ON data_document_cache TO busibox_user;
GRANT SELECT, INSERT ON data_record_history TO busibox_user;

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (for testing)
-- ============================================================================

-- To verify the migration:
-- 
-- 1. Check new columns exist:
-- SELECT column_name, data_type, column_default 
-- FROM information_schema.columns 
-- WHERE table_name = 'ingestion_files' 
-- AND column_name LIKE 'data_%' OR column_name = 'doc_type';
--
-- 2. Test creating a data document:
-- INSERT INTO ingestion_files (
--     file_id, user_id, owner_id, filename, doc_type, 
--     data_schema, data_content, visibility
-- ) VALUES (
--     gen_random_uuid(), 
--     'user-uuid'::uuid, 
--     'user-uuid'::uuid, 
--     'My Tasks Database',
--     'data',
--     '{"fields": {"name": {"type": "string", "required": true}, "done": {"type": "boolean"}}}'::jsonb,
--     '[{"id": "1", "name": "Task 1", "done": false}]'::jsonb,
--     'personal'
-- );
--
-- 3. Test querying data:
-- SELECT file_id, filename, data_content 
-- FROM ingestion_files 
-- WHERE doc_type = 'data' 
-- AND data_content @> '[{"done": false}]';
