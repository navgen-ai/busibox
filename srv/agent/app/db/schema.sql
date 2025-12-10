-- SQL DDL for initial Python/Pydantic AI agent-server schema
CREATE TABLE IF NOT EXISTS agent_definitions (
    id UUID PRIMARY KEY,
    name VARCHAR(120) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    description TEXT,
    model VARCHAR(255) NOT NULL,
    instructions TEXT NOT NULL,
    tools JSONB DEFAULT '{}'::jsonb,
    workflow JSONB,
    scopes JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_definitions (
    id UUID PRIMARY KEY,
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    schema JSONB DEFAULT '{}'::jsonb,
    entrypoint VARCHAR(255) NOT NULL,
    scopes JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id UUID PRIMARY KEY,
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    steps JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_definitions (
    id UUID PRIMARY KEY,
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rag_databases (
    id UUID PRIMARY KEY,
    name VARCHAR(120) UNIQUE NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rag_documents (
    id UUID PRIMARY KEY,
    rag_database_id UUID REFERENCES rag_databases(id) ON DELETE CASCADE,
    path VARCHAR(255) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_documents_db ON rag_documents(rag_database_id);

CREATE TABLE IF NOT EXISTS run_records (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL,
    workflow_id UUID,
    status VARCHAR(50) DEFAULT 'pending',
    input JSONB DEFAULT '{}'::jsonb,
    output JSONB,
    events JSONB DEFAULT '[]'::jsonb,
    created_by VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS token_grants (
    id UUID PRIMARY KEY,
    subject VARCHAR(255) NOT NULL,
    scopes JSONB DEFAULT '[]'::jsonb,
    token TEXT NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_grants_subject ON token_grants(subject);
