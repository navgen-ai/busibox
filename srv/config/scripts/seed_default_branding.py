#!/usr/bin/env python3
"""
Seed default branding and platform config into the config-api database.

Run once after initial deployment to populate branding defaults that were
previously stored in data-api's busibox-portal-config document.

Usage:
    python scripts/seed_default_branding.py

Environment:
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

import asyncio
import os
import sys

import asyncpg

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "config")
POSTGRES_USER = os.getenv("POSTGRES_USER", "busibox_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "devpassword")

DEFAULT_BRANDING = {
    "companyName": "Busibox Portal",
    "siteName": "Busibox Portal",
    "slogan": "How about a nice game of chess?",
    "logoUrl": "",
    "faviconUrl": "",
    "primaryColor": "#000000",
    "secondaryColor": "#8B0000",
    "textColor": "#FFFFFF",
    "addressLine1": "Cheyenne Mountain",
    "addressLine2": "",
    "addressCity": "",
    "addressState": "NV",
    "addressZip": "",
    "addressCountry": "USA",
    "supportEmail": "",
    "supportPhone": "",
    "customCss": "",
    "setupComplete": "false",
}


async def main():
    conn = await asyncpg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )

    print(f"[SEED] Connected to {POSTGRES_DB}@{POSTGRES_HOST}")

    # Ensure schema exists (idempotent)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from schema import get_config_schema
    schema = get_config_schema()
    await schema.apply(conn)
    print("[SEED] Schema applied")

    # Seed branding defaults
    count = 0
    for key, value in DEFAULT_BRANDING.items():
        result = await conn.execute(
            """
            INSERT INTO config_entries (key, value, scope, tier, category, description)
            VALUES ($1, $2, 'branding', 'public', 'branding', $3)
            ON CONFLICT (key, COALESCE(app_id, '')) DO NOTHING
            """,
            key,
            value,
            f"Portal branding: {key}",
        )
        if result.endswith("1"):
            count += 1
            print(f"  + {key} = {value[:40]}")

    print(f"[SEED] Inserted {count} branding entries ({len(DEFAULT_BRANDING) - count} already existed)")

    # Seed recommended categories as empty placeholders (admin can populate later)
    platform_categories = [
        ("smtp_host", "", False, "smtp", "SMTP server hostname"),
        ("smtp_port", "587", False, "smtp", "SMTP server port"),
        ("smtp_user", "", False, "smtp", "SMTP username"),
        ("smtp_password", "", True, "smtp", "SMTP password"),
        ("smtp_from_email", "", False, "smtp", "Default from email address"),
        ("openai_api_key", "", True, "api_keys", "OpenAI API key"),
    ]

    plat_count = 0
    for key, value, encrypted, category, description in platform_categories:
        result = await conn.execute(
            """
            INSERT INTO config_entries (key, value, encrypted, scope, tier, category, description)
            VALUES ($1, $2, $3, 'platform', 'admin', $4, $5)
            ON CONFLICT (key, COALESCE(app_id, '')) DO NOTHING
            """,
            key,
            value,
            encrypted,
            category,
            description,
        )
        if result.endswith("1"):
            plat_count += 1

    print(f"[SEED] Inserted {plat_count} platform config placeholders")

    await conn.close()
    print("[SEED] Done")


if __name__ == "__main__":
    asyncio.run(main())
