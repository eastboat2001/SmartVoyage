"""
功能：实现 skill 资产的解析和渲染构建逻辑。
作用：把 SKILL.md、assets 和 references 解析成可用于 Prompt 的文本对象。
实现方式：扫描 markdown 资产、解析 include 指令并缓存构建结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate


INCLUDE_PATTERN = re.compile(r"\[\[include:(?P<path>[^\]]+)\]\]")


class PromptBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptBuildResult:
    prompt: ChatPromptTemplate
    loaded_files: tuple[str, ...]


class PromptSkillBuilder:
    """Build ChatPromptTemplate objects from skill assets with local and contextual references."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def build_template(
        self,
        *,
        skill_name: str,
        template_path: str,
        selected_references: tuple[str, ...] = (),
    ) -> PromptBuildResult:
        loaded_files: list[str] = []
        text = self._resolve_includes(
            self.base_dir / skill_name / template_path,
            root_dir=self.base_dir / skill_name,
            ancestry=(),
            loaded_files=loaded_files,
        )
        dynamic_sections: list[str] = []
        for reference_path in selected_references:
            dynamic_sections.append(
                self._resolve_includes(
                    self.base_dir / skill_name / reference_path,
                    root_dir=self.base_dir / skill_name,
                    ancestry=(),
                    loaded_files=loaded_files,
                ).strip()
            )

        final_text = text.strip()
        if dynamic_sections:
            final_text += "\n\n补充规则：\n" + "\n".join(
                section for section in dynamic_sections if section
            )

        return PromptBuildResult(
            prompt=ChatPromptTemplate.from_template(final_text),
            loaded_files=tuple(dict.fromkeys(loaded_files)),
        )

    def _resolve_includes(
        self,
        path: Path,
        *,
        root_dir: Path,
        ancestry: tuple[Path, ...],
        loaded_files: list[str],
    ) -> str:
        normalized = path.resolve()
        if normalized in ancestry:
            cycle = " -> ".join(str(item) for item in (*ancestry, normalized))
            raise PromptBuildError(f"Prompt include cycle detected: {cycle}")
        if not normalized.exists():
            raise PromptBuildError(f"Prompt file not found: {normalized}")
        loaded_files.append(str(normalized))

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
                loaded_files=loaded_files,
            )

        return INCLUDE_PATTERN.sub(replacer, raw)

    @staticmethod
    @lru_cache(maxsize=128)
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8")
