import importlib
import inspect
import pkgutil
import re
from functools import lru_cache

from pydantic import BaseModel

from orion.contracts.tools import ToolSelection
from ._base import Category


class ToolRegistry:
    def __init__(self, categories):
        self.categories = {}
        self.tools = {}
        for category in categories:
            if not isinstance(category, Category) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", category.id):
                raise ValueError("Invalid tool category")
            if category.id in self.categories or not category.name or not category.description:
                raise ValueError(f"Duplicate or invalid category: {category.id}")
            self.categories[category.id] = category
            for tool in category.tools:
                if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tool.id) or tool.id in self.tools
                    or not tool.name or not tool.description or not 0 < tool.timeout_seconds <= 30
                    or not inspect.iscoroutinefunction(tool.handler)
                    or not issubclass(tool.input_model, BaseModel)):
                    raise ValueError(f"Duplicate or invalid tool: {tool.id}")
                tool.schema()
                self.tools[tool.id] = tool

    def validate_selection(self, selection: ToolSelection):
        unknown = (selection.categories.keys() - self.categories.keys()) | (selection.functions.keys() - self.tools.keys())
        if unknown:
            raise ValueError(f"Unknown tools/categories: {', '.join(sorted(unknown))}")
        return selection

    def enabled(self, selection: ToolSelection) -> list[str]:
        return [tool.id for cat in self.categories.values() if selection.categories.get(cat.id, False)
                for tool in cat.tools if selection.functions.get(tool.id, False)]

    def catalog(self):
        return [{"id": cat.id, "name": cat.name, "description": cat.description,
                 "functions": [{"id": tool.id, "name": tool.name, "description": tool.description}
                               for tool in cat.tools]} for cat in self.categories.values()]


@lru_cache(maxsize=1)
def get_registry() -> ToolRegistry:
    from orion.worker import tools
    categories = []
    for module in sorted(pkgutil.iter_modules(tools.__path__), key=lambda item: item.name):
        if module.name.startswith("_"):
            continue
        loaded = importlib.import_module(f"{tools.__name__}.{module.name}")
        categories.append(getattr(loaded, "CATEGORY", None))
    return ToolRegistry(categories)
