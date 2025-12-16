#!/usr/bin/env python3
"""
Quick script to fix the dispatcher_decision_log timestamp column timezone issue.
This can be run directly without needing alembic.
"""

import asyncio
import os
import sys
from sqlalchemy.ext.asyncio import create_async_engine

async def fix_timezone():
    """Apply the timezone fix to dispatcher_decision_log table"""
    
    # Get database URL from environment
    db_url = os.getenv('DATABASE_URL', 'postgresql+asyncpg://busibox_user:busibox_pass@localhost:5432/busibox')
    
    print(f"Connecting to database...")
    engine = create_async_engine(db_url, echo=True)
    
    try:
        async with engine.begin() as conn:
            print("Altering dispatcher_decision_log.timestamp column...")
            await conn.execute("""
                ALTER TABLE dispatcher_decision_log 
                ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE
            """)
            print("✓ Successfully updated timestamp column to TIMESTAMP WITH TIME ZONE")
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    
    return 0

if __name__ == '__main__':
    sys.exit(asyncio.run(fix_timezone()))
