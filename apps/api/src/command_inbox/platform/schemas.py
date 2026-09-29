"""Request bodies for the platform API (mirrors packages/contracts/src/platform-api.ts)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from command_inbox.schemas.base import CamelModel
from command_inbox.schemas.requests import Email

Domain = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
    Field(max_length=253, pattern=r"^([a-z0-9-]{1,63}\.)+[a-z]{2,63}$"),
]


class DevLoginBody(CamelModel):
    email: Email


class TenantLimitsBody(CamelModel):
    mailboxes: Annotated[int, Field(ge=1, le=50)] = 1
    seats: Annotated[int, Field(ge=1, le=5000)] = 25
    monthly_mail: Annotated[int, Field(ge=100, le=10_000_000)] = 20_000
    model_spend_cap_minor: Annotated[int, Field(ge=0, le=1_000_000_000)] = 5_000_000
    storage_gb: Annotated[int, Field(ge=1, le=10_000)] = 50
    api_per_minute: Annotated[int, Field(ge=60, le=100_000)] = 3000


class AdminContact(CamelModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    email: Email


class CreateTenantBody(CamelModel):
    slug: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    legal_name: Annotated[str, Field(min_length=1, max_length=200)]
    region: Annotated[str, Field(min_length=1, max_length=40)]
    data_residency: Annotated[str, Field(max_length=200)] = ""
    plan: Literal["Pilot", "Standard", "Enterprise"]
    locale: Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    time_zone: Annotated[str, Field(min_length=1, max_length=64)]
    support_email: Email | None = None
    email_domains: Annotated[list[Domain], Field(min_length=1, max_length=20)]
    limits: TenantLimitsBody = Field(default_factory=TenantLimitsBody)
    admin: AdminContact
    provision: bool = True


class TenantReasonBody(CamelModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class ReinviteBody(CamelModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    email: Email
