"""
功能：导出核心运行时常用对象。
作用：为外层模块提供配置、日志和 Prompt 构建器的统一导入入口。
实现方式：通过轻量 re-export 收敛核心基础设施接口。
"""

from core.config import Config
from core.prompts import SmartVoyagePrompts
from core.logging import logger

__all__ = ['Config', 'SmartVoyagePrompts', 'logger']

