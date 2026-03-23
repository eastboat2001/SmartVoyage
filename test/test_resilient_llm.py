import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from utils.resilient_llm import ResilientModelInvoker


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
    @patch('utils.resilient_llm.ResilientModelInvoker._build_fallback_model', return_value=None)
    @patch('utils.resilient_llm.build_chat_model', return_value=object())
    def test_invoke_with_models_retries_on_invalid_structured_result(self, _mock_model, _mock_fallback):
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
