from __future__ import annotations

from pydantic import BaseModel, Field


class LogLevels(BaseModel):
    class Config:
        extra = "allow"

    @classmethod
    def default(cls):
        from log.logger import LogLevel

        return cls(**{level.name: True for level in LogLevel})

    def as_dict(self) -> dict[str, bool]:
        return self.model_dump()


class LogComponents(BaseModel):
    class Config:
        extra = "allow"

    def as_dict(self) -> dict[str, bool]:
        return self.model_dump()


class LogSettings(BaseModel):
    enabled: bool = Field(default=True, description="Logger master switch")
    levels: LogLevels = Field(default_factory=LogLevels.default, description="Log seviyesi bazlı aç/kapa")
    components: LogComponents = Field(default_factory=LogComponents, description="Component bazlı log kontrolü")
