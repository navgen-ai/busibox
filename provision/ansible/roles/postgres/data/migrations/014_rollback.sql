-- Rollback migration 014
--
-- Re-add NOT NULL constraints removed for custom service support.
-- WARNING: Will fail if any rows have NULL values in these columns.

ALTER TABLE deployment_configs
    ALTER COLUMN github_connection_id SET NOT NULL,
    ALTER COLUMN github_repo_owner SET NOT NULL,
    ALTER COLUMN github_repo_name SET NOT NULL,
    ALTER COLUMN deploy_path SET NOT NULL,
    ALTER COLUMN port SET NOT NULL;
