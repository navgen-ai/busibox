-- Migration 014: Support custom service secrets
--
-- Custom services (appMode: "custom") need deployment_configs rows to store
-- encrypted secrets via app_secrets, but they don't use github_connections
-- for deployment (they bring their own repo URL / token at deploy time).
--
-- Changes:
--   - Make github_connection_id nullable (custom services don't have one)
--   - Make github_repo_owner, github_repo_name, deploy_path nullable
--   - Make port nullable (custom services define ports in their compose file)

ALTER TABLE deployment_configs
    ALTER COLUMN github_connection_id DROP NOT NULL,
    ALTER COLUMN github_repo_owner DROP NOT NULL,
    ALTER COLUMN github_repo_name DROP NOT NULL,
    ALTER COLUMN deploy_path DROP NOT NULL,
    ALTER COLUMN port DROP NOT NULL;
