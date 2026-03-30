"""
功能：实现本地 Skill Runtime。
作用：按 role、capability 和 flags 在运行时选择合适的 skill 资产。
实现方式：加载 skill manifest，解析条件引用，并向上层返回可格式化 Prompt 模板。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from langchain_core.prompts import ChatPromptTemplate

from core.logging import logger
from skills.builder import PromptBuildError, PromptSkillBuilder


@dataclass(frozen=True)
class SkillBuildContext:
    flags: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_flags(cls, *flags: str) -> "SkillBuildContext":
        return cls(flags=frozenset(flag for flag in flags if flag))

    def has(self, flag: str) -> bool:
        return flag in self.flags


@dataclass(frozen=True)
class SkillManifest:
    skill_name: str
    description: str
    owner_roles: tuple[str, ...]
    capabilities: tuple[str, ...]
    entry_assets: dict[str, str]
    default_references: dict[str, tuple[str, ...]]
    conditional_references: dict[str, dict[str, tuple[str, ...]]]


class SkillRegistry:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._manifests = self._load_manifests()

    def all(self) -> tuple[SkillManifest, ...]:
        return self._manifests

    def find(self, *, role: str, capability: str) -> SkillManifest:
        matches = [
            manifest
            for manifest in self._manifests
            if role in manifest.owner_roles and capability in manifest.capabilities
        ]
        if not matches:
            known = sorted(
                {
                    (owner_role, capability_name)
                    for manifest in self._manifests
                    for owner_role in manifest.owner_roles
                    for capability_name in manifest.capabilities
                }
            )
            raise KeyError(
                f"Unknown skill for role={role}, capability={capability}. Known mappings: {known}"
            )
        if len(matches) > 1:
            names = [manifest.skill_name for manifest in matches]
            raise KeyError(
                f"Ambiguous skills for role={role}, capability={capability}: {names}"
            )
        return matches[0]

    def _load_manifests(self) -> tuple[SkillManifest, ...]:
        manifests: list[SkillManifest] = []
        for skill_dir in sorted(path for path in self.base_dir.iterdir() if path.is_dir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            manifests.append(self._parse_manifest(skill_dir.name, skill_file))
        return tuple(manifests)

    def _parse_manifest(self, skill_name: str, skill_file: Path) -> SkillManifest:
        metadata = _parse_frontmatter(skill_file)

        def normalize_paths_map(raw: Any) -> dict[str, tuple[str, ...]]:
            result: dict[str, tuple[str, ...]] = {}
            if not isinstance(raw, dict):
                return result
            for key, value in raw.items():
                if isinstance(value, str):
                    result[str(key)] = (value,)
                elif isinstance(value, list):
                    result[str(key)] = tuple(str(item) for item in value)
            return result

        conditional: dict[str, dict[str, tuple[str, ...]]] = {}
        raw_conditional = metadata.get("conditional_references", {})
        if isinstance(raw_conditional, dict):
            for capability, conditions in raw_conditional.items():
                conditional[str(capability)] = normalize_paths_map(conditions)

        return SkillManifest(
            skill_name=str(metadata.get("name") or skill_name),
            description=str(metadata.get("description") or "").strip(),
            owner_roles=tuple(str(item) for item in metadata.get("owner_roles", [])),
            capabilities=tuple(str(item) for item in metadata.get("capabilities", [])),
            entry_assets={
                str(key): str(value)
                for key, value in (metadata.get("entry_assets", {}) or {}).items()
            },
            default_references=normalize_paths_map(metadata.get("default_references", {})),
            conditional_references=conditional,
        )


class SkillRuntime:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or Path(__file__).resolve().parent)
        self.project_root = self.base_dir.parent
        self.registry = SkillRegistry(self.base_dir)
        self.builder = PromptSkillBuilder(self.base_dir)

    def list_skill_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "name": manifest.skill_name,
                "description": manifest.description,
                "owner_roles": list(manifest.owner_roles),
                "capabilities": list(manifest.capabilities),
            }
            for manifest in self.registry.all()
        ]

    def build(
        self,
        *,
        role: str,
        capability: str,
        build_context: SkillBuildContext | None = None,
    ) -> ChatPromptTemplate:
        manifest = self.registry.find(role=role, capability=capability)
        try:
            template_path = manifest.entry_assets[capability]
        except KeyError as exc:
            raise KeyError(
                f"Skill {manifest.skill_name} missing entry asset for capability={capability}"
            ) from exc

        selected_references = list(manifest.default_references.get(capability, ()))
        conditional_map = manifest.conditional_references.get(capability, {})
        if build_context is not None:
            for flag, paths in conditional_map.items():
                if build_context.has(flag):
                    selected_references.extend(paths)

        result = self.builder.build_template(
            skill_name=manifest.skill_name,
            template_path=template_path,
            selected_references=tuple(dict.fromkeys(selected_references)),
        )
        loaded_files = [self._display_path(path) for path in result.loaded_files]
        logger.info(
            "SkillRuntime 加载 skill: "
            f"role={role}, "
            f"capability={capability}, "
            f"skill={manifest.skill_name}, "
            f"flags={sorted((build_context.flags if build_context else frozenset()))}, "
            f"loaded_files={loaded_files}"
        )
        return result.prompt

    def _display_path(self, path: str) -> str:
        normalized = Path(path)
        try:
            return normalized.resolve().relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return normalized.name


@lru_cache(maxsize=32)
def _parse_frontmatter(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise PromptBuildError(f"Skill frontmatter missing: {path}")
    _, rest = raw.split("---\n", 1)
    frontmatter, _, _ = rest.partition("\n---\n")
    data = yaml.safe_load(frontmatter) or {}
    if not isinstance(data, dict):
        raise PromptBuildError(f"Invalid skill frontmatter in: {path}")
    return data


skill_runtime = SkillRuntime()
