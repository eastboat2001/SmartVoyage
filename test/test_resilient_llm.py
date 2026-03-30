"""
功能：验证 ResilientModelInvoker 的重试与结果校验逻辑。
作用：确保结构化模型调用失败时会按预期重试与恢复。
实现方式：使用 mock 链和补丁模型模拟失败与成功分支。
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from llm.resilient_llm import ResilientModelInvoker


class _DummyChain:
    def __init__(self, results):
        self._results = results

    def invoke(self, _payload):
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _SchemaLikeResult:
    def model_dump(self):
        return {"ok": True}


class ResilientModelInvokerTest(unittest.TestCase):
    @patch('llm.resilient_llm.ResilientModelInvoker._build_light_model_spec', return_value=None)
    @patch('llm.resilient_llm.ResilientModelInvoker._build_fallback_model_spec', return_value=None)
    @patch('llm.resilient_llm.build_chat_model', return_value=object())
    def test_invoke_with_models_retries_on_invalid_structured_result(self, _mock_model, _mock_fallback, _mock_light):
        invoker = ResilientModelInvoker(Config())
        results = [None, _SchemaLikeResult()]

        outcome = invoker._invoke_with_models(
            description='structured retry test',
            retries=2,
            factory=lambda _model: _DummyChain(results),
            payload={},
            validate_result=lambda result: result is not None and hasattr(result, 'model_dump'),
            invalid_result_message='结构化输出为空或不符合预期',
        )

        self.assertIsInstance(outcome, _SchemaLikeResult)
        self.assertEqual(results, [])


if __name__ == '__main__':
    unittest.main()
