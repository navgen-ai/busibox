#!/usr/bin/env python3
"""
Seed built-in agents into the database.

This script creates the standard built-in agents that should be available to all users.
Run this after database migrations to populate the agent_definitions table.

Usage:
    python scripts/seed_builtin_agents.py
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.config.database import get_session
from app.models.domain import AgentDefinition


BUILTIN_AGENTS = [
    {
        "name": "chat",
        "display_name": "Chat Assistant",
        "description": "General purpose chat agent for answering questions and having conversations",
        "model": "chat",
        "instructions": """You are a helpful AI assistant. Provide clear, concise, and accurate responses to user queries.
        
Be conversational and friendly while maintaining professionalism. If you don't know something, say so rather than making up information.""",
        "tools": {"names": []},
        "is_active": True,
        "is_builtin": True,
    },
    {
        "name": "research",
        "display_name": "Research Assistant",
        "description": "Research agent with web search and document analysis capabilities",
        "model": "research",
        "instructions": """You are a research assistant that helps users find and analyze information.

Use web search to find current information, and analyze documents when provided. Cite your sources and provide comprehensive, well-researched answers.""",
        "tools": {"names": ["search", "rag"]},
        "is_active": True,
        "is_builtin": True,
    },
    {
        "name": "document-analyst",
        "display_name": "Document Analyst",
        "description": "Specialized agent for analyzing and extracting insights from documents",
        "model": "agent",
        "instructions": """You are a document analysis expert. Help users understand, summarize, and extract insights from their documents.

When analyzing documents:
- Provide clear summaries
- Identify key points and themes
- Answer specific questions about document content
- Compare and contrast multiple documents when requested""",
        "tools": {"names": ["rag", "ingest"]},
        "is_active": True,
        "is_builtin": True,
    },
    {
        "name": "web-researcher",
        "display_name": "Web Researcher",
        "description": "Agent specialized in finding and synthesizing information from the web",
        "model": "research",
        "instructions": """You are a web research specialist. Use web search to find current, relevant information and provide comprehensive answers.

Always:
- Search for current information
- Cite your sources
- Provide multiple perspectives when relevant
- Acknowledge when information might be outdated or uncertain""",
        "tools": {"names": ["search"]},
        "is_active": True,
        "is_builtin": True,
    },
]


async def seed_builtin_agents():
    """Seed built-in agents into the database."""
    async for session in get_session():
        try:
            print("🌱 Seeding built-in agents...")
            
            for agent_data in BUILTIN_AGENTS:
                # Check if agent already exists
                stmt = select(AgentDefinition).where(
                    AgentDefinition.name == agent_data["name"],
                    AgentDefinition.is_builtin.is_(True)
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                
                if existing:
                    print(f"  ⏭️  {agent_data['name']} already exists, skipping...")
                    continue
                
                # Create new built-in agent
                agent = AgentDefinition(**agent_data)
                session.add(agent)
                print(f"  ✅ Created {agent_data['name']} ({agent_data['display_name']})")
            
            await session.commit()
            print("\n✨ Built-in agents seeded successfully!")
            
        except Exception as e:
            print(f"\n❌ Error seeding built-in agents: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
            break  # Only use first session


if __name__ == "__main__":
    asyncio.run(seed_builtin_agents())
