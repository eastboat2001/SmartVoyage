"""
功能：验证按阶段轻重模型路由与 fallback 行为。
作用：确保分模型优化策略命中正确阶段且失败时能回退。
实现方式：通过 patch 模型工厂和调用器检查模型选择顺序。
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from llm.resilient_llm import ResilientModelInvoker


class _DummySchema:
    pass


class _DummyStructuredResult:
    def __init__(self, source: str):
        self.source = source

    def model_dump(self):
        return {"source": self.source}


class _FakeModel:
    def __init__(self, name: str, calls: list[str], *, fail: bool = False):
        self.name = name
        self.calls = calls
        self.fail = fail

    def invoke(self, payload):
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return _DummyStructuredResult(self.name)


class _FakePrompt:
    def __or__(self, model):
        return model


class TaskModelRoutingTest(unittest.TestCase):
    def setUp(self):
        self.env_backup = os.environ.copy()
        os.environ['SMARTVOYAGE_MODEL_NAME'] = 'primary-model'
        os.environ['SMARTVOYAGE_FALLBACK_PROVIDER'] = 'openai_compatible'
        os.environ['SMARTVOYAGE_FALLBACK_MODEL_NAME'] = 'fallback-model'
        os.environ['SMARTVOYAGE_LIGHT_MODEL_PROVIDER'] = 'openai_compatible'
        os.environ['SMARTVOYAGE_LIGHT_MODEL_NAME'] = 'light-model'
        os.environ['SMARTVOYAGE_LIGHT_MODEL_PHASES'] = 'intent_recognition,weather_plan,ticket_plan,order_date_resolution,order_action_classify'

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)

    @patch('llm.resilient_llm.build_chat_model')
    def test_iter_models_prefers_light_for_whitelisted_phase(self, mock_build_chat_model):
        models = {
            'primary-model': object(),
            'fallback-model': object(),
            'light-model': object(),
        }
        mock_build_chat_model.side_effect = lambda config, **kwargs: models[kwargs['model_name']]

        invoker = ResilientModelInvoker(Config())

        labels = [label for label, _model in invoker._iter_models('intent_recognition')]
        non_light_labels = [label for label, _model in invoker._iter_models('decision_plan')]

        self.assertEqual(labels, ['light', 'primary', 'fallback'])
        self.assertEqual(non_light_labels, ['primary', 'fallback'])

    @patch('llm.resilient_llm.build_chat_model')
    def test_iter_models_prefers_light_for_order_action_classify(self, mock_build_chat_model):
        models = {
            'primary-model': object(),
            'fallback-model': object(),
            'light-model': object(),
        }
        mock_build_chat_model.side_effect = lambda config, **kwargs: models[kwargs['model_name']]

        invoker = ResilientModelInvoker(Config())

        labels = [label for label, _model in invoker._iter_models('order_action_classify')]

        self.assertEqual(labels, ['light', 'primary', 'fallback'])

    @patch('llm.resilient_llm.build_structured_llm', side_effect=lambda model, schema: model)
    @patch('llm.resilient_llm.build_chat_model')
    def test_invoke_structured_falls_back_from_light_to_primary(self, mock_build_chat_model, _mock_structured):
        calls: list[str] = []
        models = {
            'primary-model': _FakeModel('primary-model', calls),
            'fallback-model': _FakeModel('fallback-model', calls),
            'light-model': _FakeModel('light-model', calls, fail=True),
        }
        mock_build_chat_model.side_effect = lambda config, **kwargs: models[kwargs['model_name']]

        invoker = ResilientModelInvoker(Config())
        result = invoker.invoke_structured(
            _FakePrompt(),
            _DummySchema,
            {'query': '现在几点'},
            description='测试结构化调用',
            phase_name='intent_recognition',
        )

        self.assertEqual(result.source, 'primary-model')
        self.assertEqual(calls[:3], ['light-model', 'light-model', 'primary-model'])


if __name__ == '__main__':
    unittest.main()
