"""
功能：定义日期、时间差和 Decimal 的 JSON 序列化规则。
作用：保证 MCP 返回结果能稳定转成 JSON 响应。
实现方式：提供默认编码函数和自定义 JSONEncoder 类处理常见非原生类型。
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal


def default_encoder(obj):
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, date):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


class DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(obj, date):
            return obj.strftime("%Y-%m-%d")
        if isinstance(obj, timedelta):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)
