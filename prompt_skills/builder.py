from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate


INCLUDE_PATTERN = re.compile(r"\[\[include:(?P<path>[^\]]+)\]\]")


class PromptBuildError(RuntimeError):
    pass


class PromptSkillBuilder:
    """Build ChatPromptTemplate objects from skill assets with local includes."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def build_template(self, *, skill_name: str, template_path: str) -> ChatPromptTemplate:
        text = self._resolve_includes(
            self.base_dir / skill_name / template_path,
            root_dir=self.base_dir / skill_name,
            ancestry=(),
        )
        return ChatPromptTemplate.from_template(text.strip())

    def _resolve_includes(self, path: Path, *, root_dir: Path, ancestry: tuple[Path, ...]) -> str:
        normalized = path.resolve()
        if normalized in ancestry:
            cycle = " -> ".join(str(item) for item in (*ancestry, normalized))
            raise PromptBuildError(f"Prompt include cycle detected: {cycle}")
        if not normalized.exists():
            raise PromptBuildError(f"Prompt file not found: {normalized}")

        raw = self._read_text(normalized)

        def replacer(match: re.Match[str]) -> str:
            include_target = match.group("path").strip()
            include_path = (root_dir / include_target).resolve()
            try:
                include_path.relative_to(root_dir.resolve())
            except ValueError as exc:
                raise PromptBuildError(
                    f"Include path escapes skill root: {include_target} in {normalized}"
                ) from exc
            return self._resolve_includes(
                include_path,
                root_dir=root_dir,
                ancestry=(*ancestry, normalized),
            )

        return INCLUDE_PATTERN.sub(replacer, raw)

    @staticmethod
    @lru_cache(maxsize=128)
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8")
