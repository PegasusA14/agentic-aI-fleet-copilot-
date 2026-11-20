from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Base(BaseModel):
    # Unknown keys (network, ...) are dropped on purpose: data minimization. `installed_software` is
    # kept (name, version, publisher): it is telemetry the agent answers from.
    model_config = ConfigDict(extra="ignore", frozen=True, protected_namespaces=())


class OsInfo(_Base):
    product_name: str
    product_version: str
    hostname: str


class DeviceIdentity(_Base):
    model_name: str


class Memory(_Base):
    total_memory_bytes: int = Field(gt=0)
    used_memory_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _used_not_above_total(self) -> Memory:
        if self.used_memory_bytes > self.total_memory_bytes:
            raise ValueError("used_memory_bytes exceeds total_memory_bytes")
        return self


class DiskVolume(_Base):
    mount_point: str
    size_bytes: int = Field(gt=0)
    available_bytes: int = Field(ge=0)
    encrypted: bool

    @model_validator(mode="after")
    def _available_not_above_size(self) -> DiskVolume:
        if self.available_bytes > self.size_bytes:
            raise ValueError("available_bytes exceeds size_bytes")
        return self


class Battery(_Base):
    battery_present: bool
    condition: str | None = None
    cycle_count: int | None = None
    full_charge_capacity: int | None = None
    percentage: int | None = None


class ComplianceResult(_Base):
    check_id: str
    status: Literal["pass", "fail"]
    severity: Literal["low", "medium", "high", "critical"]


# Software names, versions and publishers reach evidence values and answer text, so their characters are
# limited at the door. A value outside these shapes quarantines the whole row, like any malformed field.
# Name: 1-60 characters, no space at either edge.
SOFTWARE_NAME_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9 ._+-]{0,58}[A-Za-z0-9._+-])?$"
_VERSION = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$"
_PUBLISHER = r"^[A-Za-z0-9](?:[A-Za-z0-9 ._,&+'-]{0,78}[A-Za-z0-9._,&+'-])?$"
MAX_SOFTWARE_PER_ROW = 200


class InstalledSoftware(_Base):
    name: str = Field(pattern=SOFTWARE_NAME_PATTERN)
    version: str = Field(pattern=_VERSION)
    publisher: str = Field(pattern=_PUBLISHER)


class TelemetryRow(_Base):
    device_id: str
    company_id: str
    employee_id: str
    collected_at: datetime
    os: OsInfo
    device_identity: DeviceIdentity
    memory: Memory
    disk_volumes: list[DiskVolume] = Field(min_length=1)
    battery: Battery | None = None
    compliance_results: list[ComplianceResult]
    installed_software: list[InstalledSoftware] = Field(max_length=MAX_SOFTWARE_PER_ROW)

    @field_validator("collected_at")
    @classmethod
    def _must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("collected_at must include a timezone")
        try:
            return value.astimezone(UTC)
        except OverflowError:
            # Pydantic only converts ValueError into a ValidationError; an extreme timestamp
            # with a non-UTC offset must be quarantined, not abort the whole parse.
            raise ValueError("collected_at is out of range") from None

    @model_validator(mode="after")
    def _unique_check_ids(self) -> TelemetryRow:
        ids = [c.check_id for c in self.compliance_results]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate check_id in compliance_results")
        return self

    @model_validator(mode="after")
    def _unique_software_names(self) -> TelemetryRow:
        # Case-insensitive, so a lookup by name can never match two different rows.
        names = [s.name.lower() for s in self.installed_software]
        if len(names) != len(set(names)):
            raise ValueError("duplicate name in installed_software")
        return self
