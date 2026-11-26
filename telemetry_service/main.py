import os
from contextlib import asynccontextmanager
from datetime import datetime
import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlmodel import Session, SQLModel, desc, select

from shared.db import engine, ensure_utc, get_session, utc_now
from shared.models import Telemetry, Validator

DEVICE_SERVICE_URL = os.getenv("DEVICE_SERVICE_URL", "http://device_registry:8000")
INCIDENT_SERVICE_URL = os.getenv("INCIDENT_SERVICE_URL", "http://incident_analytics:8000")


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Telemetry Service (Контекст 2)",
    description="Микросервис приёма телеметрии от валидаторов, проверки пороговых значений и генерации инцидентов.",
    version="2.0.0",
    lifespan=lifespan,
)


class ErrorResponse(BaseModel):
    detail: str = PydanticField(..., examples=["Валидатор не найден"])


class TelemetryCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "validator_id": 1,
                    "temperature": 54.2,
                    "voltage": 13.4,
                    "nfc_is_working": True,
                }
            ]
        }
    )

    validator_id: int = PydanticField(..., gt=0, examples=[1], description="ID валидатора из реестра устройств")
    temperature: float = PydanticField(..., ge=-50, le=150, examples=[54.2], description="Температура в °C")
    voltage: float = PydanticField(..., ge=0, le=50, examples=[13.4], description="Напряжение питания в В")
    nfc_is_working: bool = PydanticField(..., examples=[True], description="Состояние NFC-ридера")


class TelemetryRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "validator_id": 1,
                    "temperature": 54.2,
                    "voltage": 13.4,
                    "nfc_is_working": True,
                    "recorded_at": "2026-03-10T12:00:00Z",
                }
            ]
        },
    )

    id: int
    validator_id: int
    temperature: float
    voltage: float
    nfc_is_working: bool
    recorded_at: datetime


class TelemetryProcessResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "telemetry": {
                        "id": 1,
                        "validator_id": 1,
                        "temperature": 86.5,
                        "voltage": 13.1,
                        "nfc_is_working": True,
                        "recorded_at": "2026-03-10T12:05:00Z",
                    },
                    "incident_triggered": True,
                    "incident_id": 1,
                    "detected_errors": ["Перегрев (86.5°C)"],
                }
            ]
        }
    )

    telemetry: TelemetryRead
    incident_triggered: bool
    incident_id: int | None = None
    detected_errors: list[str] = []


class ValidatorStatusRead(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "validator_id": 1,
                    "status": "Online",
                    "last_seen": "2026-03-10T12:05:00Z",
                    "seconds_since_last_packet": 4.2,
                    "last_temperature": 54.2,
                    "last_voltage": 13.4,
                    "nfc_is_working": True,
                }
            ]
        }
    )

    validator_id: int
    status: str
    last_seen: datetime | None = None
    seconds_since_last_packet: float | None = None
    last_temperature: float | None = None
    last_voltage: float | None = None
    nfc_is_working: bool | None = None


def _ensure_validator_exists(validator_id: int, session: Session) -> None:
    try:
        resp = httpx.get(f"{DEVICE_SERVICE_URL}/validators/{validator_id}", timeout=3.0)
        if resp.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден в реестре")
        if resp.status_code == 200:
            return
    except httpx.RequestError:
        pass

    if not session.get(Validator, validator_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден в реестре")


@app.post(
    "/telemetry",
    tags=["Телеметрия"],
    summary="Принять пакет телеметрии",
    description="Сохраняет показания датчиков. При перегреве (>75°C), аномальном напряжении (<10.5В или >15.5В) или отказе NFC автоматически отправляет запрос на создание инцидента.",
    status_code=status.HTTP_201_CREATED,
    response_model=TelemetryProcessResponse,
    responses={404: {"model": ErrorResponse, "description": "Валидатор не найден"}},
)
def receive_telemetry(data: TelemetryCreate, session: Session = Depends(get_session)):
    _ensure_validator_exists(data.validator_id, session)

    record = Telemetry(
        validator_id=data.validator_id,
        temperature=data.temperature,
        voltage=data.voltage,
        nfc_is_working=data.nfc_is_working,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    errors: list[str] = []
    if data.temperature > 75.0:
        errors.append(f"Перегрев ({data.temperature}°C)")
    if data.voltage < 10.5 or data.voltage > 15.5:
        errors.append(f"Аномальное напряжение ({data.voltage}В)")
    if not data.nfc_is_working:
        errors.append("Отказ NFC-ридера")

    incident_triggered = False
    incident_id: int | None = None

    if errors:
        severity = "Критическая" if (len(errors) > 1 or data.temperature > 85.0) else "Высокая"
        try:
            resp = httpx.post(
                f"{INCIDENT_SERVICE_URL}/incidents",
                json={
                    "validator_id": data.validator_id,
                    "telemetry_id": record.id,
                    "error_type": ", ".join(errors),
                    "severity": severity,
                },
                timeout=3.0,
            )
            if resp.status_code in (200, 201):
                incident_triggered = True
                incident_id = resp.json().get("id")
        except Exception as exc:
            print(f"[ERROR] Не удалось отправить инцидент: {exc}")

    return TelemetryProcessResponse(
        telemetry=TelemetryRead.model_validate(record),
        incident_triggered=incident_triggered,
        incident_id=incident_id,
        detected_errors=errors,
    )


@app.get(
    "/telemetry",
    tags=["Телеметрия"],
    summary="История телеметрии",
    response_model=list[TelemetryRead],
)
def list_telemetry(
    validator_id: int | None = Query(default=None, gt=0, description="Фильтр по ID валидатора"),
    limit: int = Query(default=50, ge=1, le=500, description="Максимальное количество записей"),
    session: Session = Depends(get_session),
):
    query = select(Telemetry).order_by(desc(Telemetry.id)).limit(limit)
    if validator_id is not None:
        query = query.where(Telemetry.validator_id == validator_id)
    return session.exec(query).all()


@app.get(
    "/telemetry/status/{validator_id}",
    tags=["Телеметрия"],
    summary="Онлайн-статус валидатора",
    description="Возвращает статус 'Online', если последний пакет получен не позднее 120 секунд назад.",
    response_model=ValidatorStatusRead,
    responses={404: {"model": ErrorResponse, "description": "Валидатор не найден"}},
)
def get_validator_status(validator_id: int, session: Session = Depends(get_session)):
    _ensure_validator_exists(validator_id, session)

    latest = session.exec(
        select(Telemetry)
        .where(Telemetry.validator_id == validator_id)
        .order_by(desc(Telemetry.id))
    ).first()

    if not latest:
        return ValidatorStatusRead(validator_id=validator_id, status="Offline")

    seconds_ago = (utc_now() - ensure_utc(latest.recorded_at)).total_seconds()
    online_status = "Online" if seconds_ago <= 120 else "Offline"

    return ValidatorStatusRead(
        validator_id=validator_id,
        status=online_status,
        last_seen=latest.recorded_at,
        seconds_since_last_packet=round(seconds_ago, 1),
        last_temperature=latest.temperature,
        last_voltage=latest.voltage,
        nfc_is_working=latest.nfc_is_working,
    )


@app.delete(
    "/telemetry/{telemetry_id}",
    tags=["Телеметрия"],
    summary="Удалить запись телеметрии",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse, "description": "Запись телеметрии не найдена"}},
)
def delete_telemetry(telemetry_id: int, session: Session = Depends(get_session)):
    record = session.get(Telemetry, telemetry_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запись телеметрии не найдена")

    session.delete(record)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)