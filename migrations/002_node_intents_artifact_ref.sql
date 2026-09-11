BEGIN;

-- `ArtifactRef` carries `schema_version` and `uri` in addition to the
-- artifact_type/artifact_id/content_digest columns 001_control_plane.sql
-- already flattened onto `node_intents`. Without these two columns a
-- completed intent's `output_ref` cannot be reconstructed in full, so this
-- migration adds them (nullable: rows written before this migration ran, or
-- intents still pending, legitimately have no output yet).
ALTER TABLE optimizer_control.node_intents
    ADD COLUMN IF NOT EXISTS output_schema_version text;

ALTER TABLE optimizer_control.node_intents
    ADD COLUMN IF NOT EXISTS output_uri text;

COMMIT;
