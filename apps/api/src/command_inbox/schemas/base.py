"""API models serialise to camelCase JSON, matching the web app's contracts package."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Ok(CamelModel):
    ok: bool = True
