from pydantic import BaseModel, ConfigDict, Field, field_validator

LEGACY_WEB_TOOLS = {"search_web", "read_page", "read_pages"}


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    categories: dict[str, bool] = Field(default_factory=dict)
    functions: dict[str, bool] = Field(default_factory=dict)

    @field_validator("functions")
    @classmethod
    def migrate_tool_ids(cls, value):
        functions = dict(value)
        if functions.get('browser_control'):
            functions.setdefault('browser_tabs', True)
        legacy = LEGACY_WEB_TOOLS & functions.keys()
        if legacy:
            functions.setdefault("research_web", any(functions[name] for name in legacy))
            for name in legacy:
                functions.pop(name)
        return functions


def default_tool_selection() -> ToolSelection:
    # New plugins never become enabled merely by being installed.
    return ToolSelection(categories={"datetime": True}, functions={"get_current_time": True})
