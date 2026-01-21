#!/usr/bin/env python3
"""
Library Data Migration Script

Migrates library data from AI Portal database to ingest-api database.
This is part of the library consolidation effort to move library management
from AI Portal to ingest-api (future files-api).

Usage:
    python migrate_libraries_to_ingest.py [--dry-run] [--verbose]

Environment variables:
    AI_PORTAL_DB_URL: AI Portal database connection URL
    INGEST_DB_URL: Ingest service database connection URL

    Or individual components:
    AI_PORTAL_DB_HOST, AI_PORTAL_DB_PORT, AI_PORTAL_DB_NAME, AI_PORTAL_DB_USER, AI_PORTAL_DB_PASSWORD
    INGEST_DB_HOST, INGEST_DB_PORT, INGEST_DB_NAME, INGEST_DB_USER, INGEST_DB_PASSWORD
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import asyncpg


def get_ai_portal_db_url() -> str:
    """Get AI Portal database connection URL."""
    if url := os.environ.get("AI_PORTAL_DB_URL"):
        return url
    
    host = os.environ.get("AI_PORTAL_DB_HOST", "localhost")
    port = os.environ.get("AI_PORTAL_DB_PORT", "5432")
    dbname = os.environ.get("AI_PORTAL_DB_NAME", "ai_portal")
    user = os.environ.get("AI_PORTAL_DB_USER", "ai_portal")
    password = os.environ.get("AI_PORTAL_DB_PASSWORD", "ai_portal")
    
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def get_ingest_db_url() -> str:
    """Get ingest service database connection URL."""
    if url := os.environ.get("INGEST_DB_URL"):
        return url
    
    host = os.environ.get("INGEST_DB_HOST", "localhost")
    port = os.environ.get("INGEST_DB_PORT", "5432")
    dbname = os.environ.get("INGEST_DB_NAME", "ingest")
    user = os.environ.get("INGEST_DB_USER", "ingest")
    password = os.environ.get("INGEST_DB_PASSWORD", "ingest")
    
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


async def fetch_ai_portal_libraries(conn: asyncpg.Connection, verbose: bool = False) -> List[Dict]:
    """Fetch all libraries from AI Portal database."""
    # Note: Prisma uses quoted column names for camelCase
    query = """
        SELECT 
            id,
            name,
            "isPersonal" as is_personal,
            "userId" as user_id,
            "libraryType" as library_type,
            "createdBy" as created_by,
            "deletedAt" as deleted_at,
            "createdAt" as created_at,
            "updatedAt" as updated_at
        FROM "Library"
        ORDER BY "createdAt" ASC
    """
    
    rows = await conn.fetch(query)
    libraries = [dict(row) for row in rows]
    
    if verbose:
        print(f"  Fetched {len(libraries)} libraries from AI Portal")
        for lib in libraries:
            print(f"    - {lib['name']} (id={lib['id']}, type={lib['library_type']}, personal={lib['is_personal']})")
    
    return libraries


async def fetch_ai_portal_tag_caches(conn: asyncpg.Connection, verbose: bool = False) -> List[Dict]:
    """Fetch all library tag caches from AI Portal database."""
    query = """
        SELECT 
            id,
            "libraryId" as library_id,
            version,
            groups,
            "generatedAt" as generated_at
        FROM "LibraryTagCache"
    """
    
    rows = await conn.fetch(query)
    caches = [dict(row) for row in rows]
    
    if verbose:
        print(f"  Fetched {len(caches)} tag caches from AI Portal")
    
    return caches


async def check_ingest_library_exists(conn: asyncpg.Connection, library_id: str) -> bool:
    """Check if a library already exists in ingest database."""
    result = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM libraries WHERE id = $1)",
        library_id
    )
    return result


async def insert_library_to_ingest(
    conn: asyncpg.Connection,
    library: Dict,
    dry_run: bool = False,
    verbose: bool = False
) -> bool:
    """Insert a library into ingest database."""
    library_id = library['id']
    
    # Check if already exists
    exists = await check_ingest_library_exists(conn, library_id)
    if exists:
        if verbose:
            print(f"    Library {library_id} already exists in ingest DB, skipping")
        return False
    
    if dry_run:
        if verbose:
            print(f"    [DRY RUN] Would insert library: {library['name']} ({library_id})")
        return True
    
    await conn.execute(
        """
        INSERT INTO libraries (
            id, name, is_personal, user_id, library_type,
            created_by, deleted_at, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        library_id,
        library['name'],
        library['is_personal'],
        library['user_id'],
        library['library_type'],
        library['created_by'],
        library['deleted_at'],
        library['created_at'],
        library['updated_at']
    )
    
    if verbose:
        print(f"    Inserted library: {library['name']} ({library_id})")
    
    return True


async def insert_tag_cache_to_ingest(
    conn: asyncpg.Connection,
    cache: Dict,
    dry_run: bool = False,
    verbose: bool = False
) -> bool:
    """Insert a tag cache into ingest database."""
    cache_id = cache['id']
    library_id = cache['library_id']
    
    # Check if library exists first
    library_exists = await check_ingest_library_exists(conn, library_id)
    if not library_exists:
        if verbose:
            print(f"    Warning: Library {library_id} not found, skipping tag cache")
        return False
    
    # Check if cache already exists
    exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM library_tag_cache WHERE id = $1)",
        cache_id
    )
    if exists:
        if verbose:
            print(f"    Tag cache {cache_id} already exists, skipping")
        return False
    
    if dry_run:
        if verbose:
            print(f"    [DRY RUN] Would insert tag cache for library {library_id}")
        return True
    
    # Handle JSON encoding for groups field
    import json
    groups_json = cache['groups']
    if isinstance(groups_json, str):
        groups_json = json.loads(groups_json)
    
    await conn.execute(
        """
        INSERT INTO library_tag_cache (id, library_id, version, groups, generated_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        cache_id,
        library_id,
        cache['version'],
        json.dumps(groups_json),
        cache['generated_at']
    )
    
    if verbose:
        print(f"    Inserted tag cache for library {library_id}")
    
    return True


async def update_ingestion_files_library_ids(
    ingest_conn: asyncpg.Connection,
    portal_conn: asyncpg.Connection,
    dry_run: bool = False,
    verbose: bool = False
) -> int:
    """
    Update library_id column in ingestion_files based on AI Portal Document records.
    
    AI Portal's Document table has libraryId which maps file IDs to libraries.
    We need to propagate this to ingestion_files.library_id.
    """
    # Get document-library mappings from AI Portal
    query = """
        SELECT id, "libraryId" as library_id
        FROM "Document"
        WHERE "libraryId" IS NOT NULL
    """
    
    documents = await portal_conn.fetch(query)
    
    if verbose:
        print(f"  Found {len(documents)} documents with library assignments")
    
    updated = 0
    for doc in documents:
        file_id = doc['id']
        library_id = doc['library_id']
        
        if dry_run:
            if verbose:
                print(f"    [DRY RUN] Would update file {file_id} with library_id {library_id}")
            updated += 1
            continue
        
        # Update the ingestion_files record
        result = await ingest_conn.execute(
            """
            UPDATE ingestion_files
            SET library_id = $1
            WHERE id = $2 AND library_id IS NULL
            """,
            library_id,
            file_id
        )
        
        # Check if update happened
        if result.split()[-1] != '0':
            updated += 1
            if verbose:
                print(f"    Updated file {file_id} with library_id {library_id}")
    
    return updated


async def run_migration(dry_run: bool = False, verbose: bool = False) -> Tuple[int, int, int]:
    """
    Run the library migration.
    
    Returns:
        Tuple of (libraries_migrated, caches_migrated, files_updated)
    """
    print("=" * 60)
    print("Library Migration: AI Portal -> Ingest Service")
    print("=" * 60)
    
    if dry_run:
        print("DRY RUN MODE - No changes will be made")
    
    print()
    
    # Connect to both databases
    ai_portal_url = get_ai_portal_db_url()
    ingest_url = get_ingest_db_url()
    
    print(f"AI Portal DB: {ai_portal_url.split('@')[1] if '@' in ai_portal_url else ai_portal_url}")
    print(f"Ingest DB: {ingest_url.split('@')[1] if '@' in ingest_url else ingest_url}")
    print()
    
    portal_conn = await asyncpg.connect(ai_portal_url)
    ingest_conn = await asyncpg.connect(ingest_url)
    
    try:
        # Step 1: Migrate libraries
        print("Step 1: Migrating libraries...")
        libraries = await fetch_ai_portal_libraries(portal_conn, verbose)
        
        migrated_libs = 0
        for lib in libraries:
            if await insert_library_to_ingest(ingest_conn, lib, dry_run, verbose):
                migrated_libs += 1
        
        print(f"  Migrated {migrated_libs} libraries")
        print()
        
        # Step 2: Migrate tag caches
        print("Step 2: Migrating tag caches...")
        caches = await fetch_ai_portal_tag_caches(portal_conn, verbose)
        
        migrated_caches = 0
        for cache in caches:
            if await insert_tag_cache_to_ingest(ingest_conn, cache, dry_run, verbose):
                migrated_caches += 1
        
        print(f"  Migrated {migrated_caches} tag caches")
        print()
        
        # Step 3: Update library_id in ingestion_files
        print("Step 3: Updating library_id in ingestion_files...")
        updated_files = await update_ingestion_files_library_ids(
            ingest_conn, portal_conn, dry_run, verbose
        )
        print(f"  Updated {updated_files} file records")
        print()
        
        return migrated_libs, migrated_caches, updated_files
    
    finally:
        await portal_conn.close()
        await ingest_conn.close()


async def main():
    parser = argparse.ArgumentParser(
        description="Migrate library data from AI Portal to ingest service"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making changes"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    
    args = parser.parse_args()
    
    try:
        libs, caches, files = await run_migration(args.dry_run, args.verbose)
        
        print("=" * 60)
        print("Migration Summary")
        print("=" * 60)
        print(f"Libraries migrated: {libs}")
        print(f"Tag caches migrated: {caches}")
        print(f"Files updated: {files}")
        
        if args.dry_run:
            print()
            print("This was a DRY RUN. Run without --dry-run to apply changes.")
        
        print()
        print("Migration complete!")
        
    except asyncpg.PostgresError as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
