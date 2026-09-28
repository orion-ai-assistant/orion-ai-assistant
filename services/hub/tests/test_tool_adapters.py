import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pydantic import Field, ValidationError

from orion.worker.services.tool_runner import ToolCancelled, execute_tool
from orion.worker.tools._adapters import from_callable, from_langchain
from orion.worker.tools._base import ToolContext, ToolInput
from orion.worker.tools._registry import ToolRegistry, get_registry


class SearchInput(ToolInput):
    query: str = Field(min_length=1)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = ToolContext('user', 'chat', 'turn', AsyncMock(return_value=False))
        self.metadata = dict(id='search', name='Search', description='Search a source.',
                             input_model=SearchInput)

    async def test_lazy_factory_validation_and_argument_result_mapping(self):
        call = AsyncMock(return_value=SimpleNamespace(items=['one']))
        factory = Mock(return_value=call)
        tool = from_callable(**self.metadata, factory=factory,
                             map_input=lambda args: {'q': args.query},
                             map_output=lambda result: {'items': result.items})
        tool.schema()
        factory.assert_not_called()
        with self.assertRaises(ValidationError):
            await execute_tool(tool, '{"query":""}', self.context)
        factory.assert_not_called()
        result = await execute_tool(tool, '{"query":"hello"}', self.context)
        self.assertEqual(result, {'items': ['one']})
        factory.assert_called_once_with(self.context)
        call.assert_awaited_once_with(q='hello')

    async def test_langchain_native_async_contract(self):
        external = SimpleNamespace(ainvoke=AsyncMock(return_value={'links': []}))
        tool = from_langchain(**self.metadata, factory=lambda context: external, native_async=True)
        self.assertEqual(await execute_tool(tool, '{"query":"hello"}', self.context), {'links': []})
        external.ainvoke.assert_awaited_once_with(input={'query': 'hello'})

    async def test_sync_factory_and_invoke_run_off_event_loop(self):
        main_thread = threading.get_ident()
        threads = []
        def factory(context):
            threads.append(threading.get_ident())
            def invoke(input):
                threads.append(threading.get_ident())
                return {'query': input}
            return SimpleNamespace(invoke=invoke)
        tool = from_langchain(**self.metadata, factory=factory, native_async=False,
                              read_only=True, map_input=lambda args: args.query)
        self.assertEqual(await execute_tool(tool, '{"query":"hello"}', self.context), {'query': 'hello'})
        self.assertEqual(threads[0], threads[1])
        self.assertNotEqual(threads[0], main_thread)

    def test_thread_execution_requires_explicit_read_only(self):
        with self.assertRaisesRegex(ValueError, 'read_only'):
            from_langchain(**self.metadata, factory=Mock(), native_async=False)
        with self.assertRaisesRegex(ValueError, 'execution'):
            from_callable(**self.metadata, factory=Mock(), execution='invalid')

    async def test_missing_optional_dependency_is_only_a_call_error(self):
        factory = Mock(side_effect=ModuleNotFoundError('optional provider missing'))
        tool = from_callable(**self.metadata, factory=factory)
        from orion.worker.tools._base import Category
        registry = ToolRegistry([Category('external', 'External', 'External tools.', (tool,))])
        self.assertEqual(registry.catalog()[0]['id'], 'external')
        factory.assert_not_called()
        with self.assertRaises(ModuleNotFoundError):
            await execute_tool(tool, '{"query":"hello"}', self.context)

    async def test_cancel_before_call_and_timeout_propagate(self):
        ended = asyncio.Event()
        async def call(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        factory = Mock(return_value=call)
        tool = from_callable(**self.metadata, factory=factory, timeout_seconds=.01)
        self.context.cancelled.return_value = True
        with self.assertRaises(ToolCancelled):
            await execute_tool(tool, '{"query":"hello"}', self.context)
        factory.assert_not_called()
        self.context.cancelled.return_value = False
        with self.assertRaises(TimeoutError):
            await execute_tool(tool, '{"query":"hello"}', self.context)
        self.assertTrue(ended.is_set())

    async def test_adapter_preserves_full_output_and_requires_json(self):
        result = 'x' * 32768
        tool = from_callable(**self.metadata, factory=lambda context: AsyncMock(return_value=result))
        self.assertEqual(await execute_tool(tool, '{"query":"hello"}', self.context), result)
        tool = from_callable(**self.metadata, factory=lambda context: AsyncMock(return_value=object()))
        with self.assertRaises(TypeError):
            await execute_tool(tool, '{"query":"hello"}', self.context)

    def test_category_packages_discovered_without_adapter_as_category(self):
        get_registry.cache_clear()
        registry = get_registry()
        self.assertIn('datetime', registry.categories)
        self.assertIn('text', registry.categories)
        self.assertNotIn('_adapters', registry.categories)
