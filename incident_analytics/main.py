from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlmodel import Session, SQLModel, desc, or_, select

from shared.db import engine, ensure_utc, get_session, utc_now
from shared.models import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Notification,
    Telemetry,
    Validator,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Incident & Analytics Service (Контекст 3)",
    description="Микросервис обработки аварийных инцидентов, уведомлений и расчёта SLA-метрик ремонта.",
    version="2.0.0",
    lifespan=lifespan,
)


class ErrorResponse(BaseModel):
    detail: str = PydanticField(..., examples=["Инцидент не найден"])


class IncidentCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "validator_id": 1,
                    "telemetry_id": 1,
                    "error_type": "Перегрев (88.5°C)",
                    "severity": "Критическая",
                }
            ]
        }
    )

    validator_id: int = PydanticField(..., gt=0, examples=[1], description="ID валидатора")
    telemetry_id: int | None = PydanticField(default=None, gt=0, examples=[1], description="ID пакета телеметрии, вызвавшего инцидент")
    error_type: str = PydanticField(..., min_length=2, max_length=255, examples=["Перегрев (88.5°C)"])
    severity: IncidentSeverity = PydanticField(default=IncidentSeverity.HIGH, examples=["Критическая"])


class IncidentUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "Закрыт",
                    "engineer_comment": "Заменён кулер охлаждения и очищена вентиляция",
                }
            ]
        }
    )

    status: IncidentStatus | None = PydanticField(default=None, examples=["Закрыт"])
    severity: IncidentSeverity | None = PydanticField(default=None, examples=["Высокая"])
    engineer_comment: str | None = PydanticField(default=None, max_length=1000, examples=["Заменён кулер охлаждения"])


class NotificationRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "incident_id": 1,
                    "recipient": "service_engineer",
                    "message": "Incident #1: Validator #1 - Перегрев (88.5°C) (severity: Критическая)",
                    "is_read": False,
                    "status": "delivered",
                    "sent_at": "2026-03-10T12:05:01Z",
                }
            ]
        },
    )

    id: int
    incident_id: int
    recipient: str
    message: str
    is_read: bool
    status: str
    sent_at: datetime


class NotificationUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"is_read": True}
            ]
        }
    )

    is_read: bool = PydanticField(..., examples=[True])


class IncidentRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "validator_id": 1,
                    "telemetry_id": 1,
                    "error_type": "Перегрев (88.5°C)",
                    "severity": "Критическая",
                    "status": "Открыт",
                    "engineer_comment": None,
                    "created_at": "2026-03-10T12:05:00Z",
                    "closed_at": None,
                }
            ]
        },
    )

    id: int
    validator_id: int
    telemetry_id: int | None
    error_type: str
    severity: IncidentSeverity
    status: IncidentStatus
    engineer_comment: str | None
    created_at: datetime
    closed_at: datetime | None


class IncidentDetailRead(IncidentRead):
    notifications: list[NotificationRead] = []


class AnalyticsRead(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "total_incidents": 5,
                    "open_incidents": 1,
                    "in_progress_incidents": 1,
                    "closed_incidents": 3,
                    "unread_notifications": 2,
                    "avg_repair_seconds": 145.8,
                }
            ]
        }
    )

    total_incidents: int
    open_incidents: int
    in_progress_incidents: int
    closed_incidents: int
    unread_notifications: int
    avg_repair_seconds: float


# ======================= ИНЦИДЕНТЫ =======================

@app.post(
    "/incidents",
    tags=["Инциденты"],
    summary="Создать новый инцидент и отправить уведомление",
    status_code=status.HTTP_201_CREATED,
    response_model=IncidentRead,
    responses={
        404: {"model": ErrorResponse, "description": "Валидатор или телеметрия не найдены"},
        409: {"model": ErrorResponse, "description": "Для данного валидатора уже есть активный инцидент"},
    },
)
def create_incident(data: IncidentCreate, session: Session = Depends(get_session)):
    if not session.get(Validator, data.validator_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден")

    if data.telemetry_id is not None and not session.get(Telemetry, data.telemetry_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запись телеметрии не найдена")

    existing = session.exec(
        select(Incident).where(
            Incident.validator_id == data.validator_id,
            or_(
                Incident.status == IncidentStatus.OPEN.value,
                Incident.status == IncidentStatus.IN_PROGRESS.value,
            ),
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Активный инцидент для этого валидатора уже существует",
        )

    incident = Incident(
        validator_id=data.validator_id,
        telemetry_id=data.telemetry_id,
        error_type=data.error_type,
        severity=data.severity.value,
        status=IncidentStatus.OPEN.value,
    )
    session.add(incident)
    session.commit()
    session.refresh(incident)

    alert_text = (
        f"Incident #{incident.id}: Validator #{data.validator_id} - "
        f"{data.error_type} (severity: {data.severity.value})"
    )
    notification = Notification(
        incident_id=incident.id,
        recipient="service_engineer",
        message=alert_text,
        is_read=False,
        status="delivered",
    )
    session.add(notification)
    session.commit()

    print(f"[ALERT] {alert_text}")
    return incident


@app.get(
    "/incidents",
    tags=["Инциденты"],
    summary="Список инцидентов",
    response_model=list[IncidentRead],
)
def list_incidents(
    incident_status: IncidentStatus | None = Query(default=None, alias="status", description="Фильтр по статусу"),
    validator_id: int | None = Query(default=None, gt=0, description="Фильтр по ID валидатора"),
    severity: IncidentSeverity | None = Query(default=None, description="Фильтр по критичности"),
    session: Session = Depends(get_session),
):
    query = select(Incident).order_by(desc(Incident.id))
    if incident_status is not None:
        query = query.where(Incident.status == incident_status.value)
    if validator_id is not None:
        query = query.where(Incident.validator_id == validator_id)
    if severity is not None:
        query = query.where(Incident.severity == severity.value)
    return session.exec(query).all()


@app.get(
    "/incidents/{incident_id}",
    tags=["Инциденты"],
    summary="Детальная карточка инцидента со списком уведомлений",
    response_model=IncidentDetailRead,
    responses={404: {"model": ErrorResponse, "description": "Инцидент не найден"}},
)
def get_incident(incident_id: int, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Инцидент не найден")
    return IncidentDetailRead.model_validate(incident)


@app.patch(
    "/incidents/{incident_id}",
    tags=["Инциденты"],
    summary="Обновить инцидент (взять в работу, закрыть, добавить комментарий)",
    response_model=IncidentRead,
    responses={
        400: {"model": ErrorResponse, "description": "Инцидент уже закрыт или некорректный переход"},
        404: {"model": ErrorResponse, "description": "Инцидент не найден"},
    },
)
def update_incident(incident_id: int, data: IncidentUpdate, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Инцидент не найден")
    if incident.status == IncidentStatus.CLOSED.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Закрытый инцидент нельзя изменить")

    updates = data.model_dump(exclude_unset=True)
    new_status: IncidentStatus | None = updates.pop("status", None)
    new_severity: IncidentSeverity | None = updates.pop("severity", None)

    if new_severity is not None:
        incident.severity = new_severity.value

    if "engineer_comment" in updates:
        incident.engineer_comment = updates["engineer_comment"]

    if new_status is not None:
        incident.status = new_status.value
        if new_status == IncidentStatus.CLOSED:
            incident.closed_at = utc_now()

    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident


@app.delete(
    "/incidents/{incident_id}",
    tags=["Инциденты"],
    summary="Удалить инцидент (вместе с уведомлениями)",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse, "description": "Инцидент не найден"}},
)
def delete_incident(incident_id: int, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Инцидент не найден")

    session.delete(incident)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ======================= УВЕДОМЛЕНИЯ =======================

@app.get(
    "/notifications",
    tags=["Уведомления"],
    summary="Список уведомлений инженерам",
    response_model=list[NotificationRead],
)
def list_notifications(
    is_read: bool | None = Query(default=None, description="Фильтр по статусу прочтения"),
    incident_id: int | None = Query(default=None, gt=0, description="Фильтр по ID инцидента"),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    query = select(Notification).order_by(desc(Notification.id)).limit(limit)
    if is_read is not None:
        query = query.where(Notification.is_read == is_read)
    if incident_id is not None:
        query = query.where(Notification.incident_id == incident_id)
    return session.exec(query).all()


@app.patch(
    "/notifications/{notification_id}",
    tags=["Уведомления"],
    summary="Отметить уведомление прочитанным/непрочитанным",
    response_model=NotificationRead,
    responses={404: {"model": ErrorResponse, "description": "Уведомление не найдено"}},
)
def update_notification(
    notification_id: int,
    data: NotificationUpdate,
    session: Session = Depends(get_session),
):
    notification = session.get(Notification, notification_id)
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Уведомление не найдено")

    notification.is_read = data.is_read
    session.add(notification)
    session.commit()
    session.refresh(notification)
    return notification


# ======================= АНАЛИТИКА =======================

@app.get(
    "/analytics",
    tags=["Аналитика"],
    summary="Сводная аналитика по инцидентам и среднему времени ремонта",
    response_model=AnalyticsRead,
)
def get_analytics(session: Session = Depends(get_session)):
    all_incidents = session.exec(select(Incident)).all()
    unread_count = len(
        session.exec(select(Notification).where(Notification.is_read == False)).all()  # noqa: E712
    )

    total = len(all_incidents)
    open_count = sum(1 for i in all_incidents if i.status == IncidentStatus.OPEN.value)
    in_progress_count = sum(1 for i in all_incidents if i.status == IncidentStatus.IN_PROGRESS.value)
    closed_list = [i for i in all_incidents if i.status == IncidentStatus.CLOSED.value and i.closed_at]

    if closed_list:
        total_seconds = sum(
            (ensure_utc(i.closed_at) - ensure_utc(i.created_at)).total_seconds()
            for i in closed_list
            if i.closed_at is not None
        )
        avg_repair_seconds = round(total_seconds / len(closed_list), 1)
    else:
        avg_repair_seconds = 0.0

    return AnalyticsRead(
        total_incidents=total,
        open_incidents=open_count,
        in_progress_incidents=in_progress_count,
        closed_incidents=len(closed_list),
        unread_notifications=unread_count,
        avg_repair_seconds=avg_repair_seconds,
    )