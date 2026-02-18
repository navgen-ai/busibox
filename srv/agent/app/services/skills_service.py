"""
AgentSkills-compatible SKILL.md loader for Busibox agents.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.schemas.auth import Principal

logger = logging.getLogger(__name__)


@dataclass
class SkillDefinition:
    name: str
    description: str
    instructions: str
    path: str
    user_invocable: bool = True
    required_roles: List[str] | None = None


class SkillsService:
    """Loads SKILL.md files and exposes role-gated prompts for agents."""

    def __init__(
        self,
        enabled: bool,
        skill_dirs: List[str],
        cache_ttl_seconds: int = 60,
        global_allowed_roles: Optional[List[str]] = None,
        clawhub_enabled: bool = False,
    ):
        self.enabled = enabled
        self.skill_dirs = [d for d in skill_dirs if d]
        self.cache_ttl_seconds = max(1, cache_ttl_seconds)
        self.global_allowed_roles = [r.strip().lower() for r in (global_allowed_roles or []) if r.strip()]
        self.clawhub_enabled = clawhub_enabled
        self._last_load = 0.0
        self._cache: Dict[str, SkillDefinition] = {}

    def _refresh_if_needed(self) -> None:
        now = time.time()
        if (now - self._last_load) < self.cache_ttl_seconds and self._cache:
            return
        self._cache = self._load_all()
        self._last_load = now

    def _load_all(self) -> Dict[str, SkillDefinition]:
        skills: Dict[str, SkillDefinition] = {}
        if not self.enabled:
            return skills

        for base in self.skill_dirs:
            root = Path(os.path.expanduser(base))
            if not root.exists():
                continue
            for skill_file in root.rglob("SKILL.md"):
                skill = self._parse_skill_file(skill_file)
                if not skill or not skill.name:
                    continue
                # Keep first instance by precedence of configured dirs
                skills.setdefault(skill.name, skill)
        logger.info("Loaded %d skills from %d dirs", len(skills), len(self.skill_dirs))
        return skills

    def _parse_skill_file(self, path: Path) -> Optional[SkillDefinition]:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read skill file %s: %s", path, e)
            return None

        frontmatter, body = self._split_frontmatter(content)
        name = (frontmatter.get("name") or path.parent.name).strip()
        description = (frontmatter.get("description") or "Skill").strip()
        user_invocable = str(frontmatter.get("user-invocable", "true")).strip().lower() != "false"
        roles_raw = (frontmatter.get("required_roles") or "").strip()
        required_roles = [r.strip().lower() for r in roles_raw.split(",") if r.strip()] if roles_raw else []

        return SkillDefinition(
            name=name,
            description=description,
            instructions=body.strip(),
            path=str(path),
            user_invocable=user_invocable,
            required_roles=required_roles or None,
        )

    def _split_frontmatter(self, content: str) -> tuple[Dict[str, str], str]:
        text = content.lstrip()
        if not text.startswith("---"):
            return {}, content
        lines = text.splitlines()
        if len(lines) < 3:
            return {}, content
        try:
            end_idx = lines[1:].index("---") + 1
        except ValueError:
            return {}, content
        fm_lines = lines[1:end_idx]
        body_lines = lines[end_idx + 1 :]
        frontmatter: Dict[str, str] = {}
        for line in fm_lines:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip().lower()] = value.strip().strip('"').strip("'")
        return frontmatter, "\n".join(body_lines)

    def get_skills_for_principal(self, principal: Optional[Principal]) -> List[SkillDefinition]:
        self._refresh_if_needed()
        if not self.enabled:
            return []

        roles = set()
        if principal and principal.roles:
            roles = {r.lower() for r in principal.roles}

        # Optional global RBAC gate.
        if self.global_allowed_roles and roles.isdisjoint(set(self.global_allowed_roles)):
            return []

        out: List[SkillDefinition] = []
        for skill in self._cache.values():
            if skill.required_roles and roles.isdisjoint(set(skill.required_roles)):
                continue
            out.append(skill)
        return out

    def render_skills_prompt(self, principal: Optional[Principal]) -> str:
        skills = self.get_skills_for_principal(principal)
        if not skills:
            return ""

        lines = ["## Available Skills"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")
            if skill.instructions:
                lines.append(skill.instructions[:1200])
        if self.clawhub_enabled:
            lines.append("ClawHub integration is enabled for this environment.")
        return "\n".join(lines)


_skills_service_singleton: Optional[SkillsService] = None


def get_skills_service() -> SkillsService:
    """Build and return the singleton SkillsService from runtime settings."""
    global _skills_service_singleton
    if _skills_service_singleton is not None:
        return _skills_service_singleton

    from app.config.settings import get_settings

    settings = get_settings()
    _skills_service_singleton = SkillsService(
        enabled=settings.skills_enabled,
        skill_dirs=settings.get_skill_dirs(),
        cache_ttl_seconds=settings.skills_cache_ttl_seconds,
        global_allowed_roles=settings.get_skills_allowed_roles(),
        clawhub_enabled=settings.skills_clawhub_enabled,
    )
    return _skills_service_singleton
