from datetime import date, datetime
from enum import Enum
from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel

from shared.db import utc_now


class ValidatorStatus(str, Enum):
    IN_STOCK = "На складе"
    ON_LINE = "На линии"
    IN_REPAIR = "В ремонте"
    DECOMMISSIONED = "Списан"


class IncidentSeverity(str, Enum):
    LOW = "Низкая"
    MEDIUM = "Средняя"
    HIGH = "Высокая"
    CRITICAL = "Критическая"


class IncidentStatus(str, Enum):
    OPEN = "Открыт"
    IN_PROGRESS = "В работе"
    CLOSED = "Закрыт"


class Vehicle(SQLModel, table=True):
    __tablename__ = "vehicles"

    id: int | None = Field(default=None, primary_key=True)
    bort_number: str = Field(unique=True, index=True, max_length=32)
    route_number: str = Field(max_length=32)
    model: str = Field(max_length=64)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))

    validators: list["Validator"] = Relationship(back_populates="vehicle")


class Validator(SQLModel, table=True):
    __tablename__ = "validators"

    id: int | None = Field(default=None, primary_key=True)
    serial_number: str = Field(unique=True, index=True, max_length=64)
    model: str = Field(max_length=64)
    mac_address: str = Field(unique=True, max_length=32)
    manufacture_date: date
    firmware_version: str = Field(default="1.0.0", max_length=32)
    status: str = Field(default=ValidatorStatus.IN_STOCK.value, max_length=32)
    vehicle_id: int | None = Field(
        default=None,
        foreign_key="vehicles.id",
        ondelete="SET NULL",
        index=True,
    )
    dismount_reason: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))

    vehicle: Vehicle | None = Relationship(back_populates="validators")
    telemetry_records: list["Telemetry"] = Relationship(
        back_populates="validator",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    incidents: list["Incident"] = Relationship(
        back_populates="validator",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Telemetry(SQLModel, table=True):
    __tablename__ = "telemetry"

    id: int | None = Field(default=None, primary_key=True)
    validator_id: int = Field(
        foreign_key="validators.id",
        ondelete="CASCADE",
        index=True,
    )
    temperature: float
    voltage: float
    nfc_is_working: bool
    recorded_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))

    validator: Validator | None = Relationship(back_populates="telemetry_records")
    incidents: list["Incident"] = Relationship(back_populates="telemetry")


class Incident(SQLModel, table=True):
    __tablename__ = "incidents"

    id: int | None = Field(default=None, primary_key=True)
    validator_id: int = Field(
        foreign_key="validators.id",
        ondelete="CASCADE",
        index=True,
    )
    telemetry_id: int | None = Field(
        default=None,
        foreign_key="telemetry.id",
        ondelete="SET NULL",
        index=True,
    )
    error_type: str = Field(max_length=255)
    severity: str = Field(default=IncidentSeverity.HIGH.value, max_length=32)
    status: str = Field(default=IncidentStatus.OPEN.value, max_length=32)
    engineer_comment: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))
    closed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    validator: Validator | None = Relationship(back_populates="incidents")
    telemetry: Telemetry | None = Relationship(back_populates="incidents")
    notifications: list["Notification"] = Relationship(
        back_populates="incident",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: int | None = Field(default=None, primary_key=True)
    incident_id: int = Field(
        foreign_key="incidents.id",
        ondelete="CASCADE",
        index=True,
    )
    recipient: str = Field(default="service_engineer", max_length=64)
    message: str
    is_read: bool = Field(default=False)
    status: str = Field(default="delivered", max_length=32)
    sent_at: datetime = Field(default_factory=utc_now, sa_type=DateTime(timezone=True))

    incident: Incident | None = Relationship(back_populates="notifications")