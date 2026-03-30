"""
功能：导出 Skill Runtime 相关核心对象。
作用：为 Prompt 构建层提供统一的 skill 装配入口。
实现方式：在包入口 re-export SkillRuntime、Manifest 和构建上下文类型。
"""

from skills.runtime import SkillBuildContext, SkillManifest, SkillRuntime, skill_runtime

__all__ = ["SkillBuildContext", "SkillManifest", "SkillRuntime", "skill_runtime"]
