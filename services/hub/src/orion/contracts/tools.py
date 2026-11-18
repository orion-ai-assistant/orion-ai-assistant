from pydantic import BaseModel, ConfigDict, Field, field_validator

LEGACY_WEB_TOOLS = {"search_web", "read_page", "read_pages"}
# The memory tools' Turkish ids before they were renamed (a saved selection keeps its choices under the new ids).
RENAMED_TOOLS = {"hafizada_ara": "search_memory", "hafizaya_ekle": "add_memory", "hafizadan_sil": "delete_memory",
                 "hafizadaki_medyayi_ac": "open_memory_files", "hafiza_baglami": "memory_context"}
# `add_memory` became `remember` (the chat model only hands the user's words over; the background memory writer files them).
SPLIT_TOOLS = {"add_memory": ("remember",)}
# The tools that touch the memory: offered ONLY in "Asistanla konuş" conversations, never in a normal chat.
MEMORY_TOOLS = frozenset({"search_memory", "remember", "delete_memory", "open_memory_files", "memory_context"})


def expand_split_tools(names):
    """`names` (tool ids) with every split tool replaced by the tools it became."""
    out = []
    for name in names:
        out.extend(SPLIT_TOOLS.get(name, (name,)))
    return out


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    categories: dict[str, bool] = Field(default_factory=dict)
    functions: dict[str, bool] = Field(default_factory=dict)

    @field_validator("categories")
    @classmethod
    def migrate_categories(cls, value):
        categories = dict(value)
        if categories.get('youtube') or categories.get('ytmusic'):
            categories.setdefault('media', True)
        categories.pop('youtube', None)
        categories.pop('ytmusic', None)
        return categories

    @field_validator("functions")
    @classmethod
    def migrate_tool_ids(cls, value):
        functions = dict(value)
        for old, new in RENAMED_TOOLS.items():
            if old in functions:
                functions.setdefault(new, functions.pop(old))
        for old, parts in SPLIT_TOOLS.items():
            if old in functions:
                chosen = functions.pop(old)
                for new in parts:
                    functions.setdefault(new, chosen)
        if functions.get('browser_control'):
            functions.setdefault('browser_tabs', True)
        if functions.get('youtube_control') or functions.get('ytmusic_control'):
            functions.setdefault('media_control', True)
        functions.pop('youtube_control', None)
        functions.pop('ytmusic_control', None)
        legacy = LEGACY_WEB_TOOLS & functions.keys()
        if legacy:
            functions.setdefault("research_web", any(functions[name] for name in legacy))
            for name in legacy:
                functions.pop(name)
        return functions


def default_tool_selection() -> ToolSelection:
    # New plugins never become enabled merely by being installed.
    # The memory tools are the exception: they only touch what the user chose to save, so they are on from the start.
    return ToolSelection(categories={"datetime": True, "memory": True},
                         functions={"get_current_time": True, "search_memory": True,
                                    "remember": True, "delete_memory": True,
                                    "open_memory_files": True, "memory_context": True})
