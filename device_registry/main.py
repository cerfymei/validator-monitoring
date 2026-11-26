from contextlib import asynccontextmanager
from datetime import date, datetime
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlmodel import Session, SQLModel, select

from shared.db import engine, get_session, utc_now
from shared.models import Validator, ValidatorStatus, Vehicle


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Device Registry Service (Контекст 1)",
    description="Микросервис управления парком транспортных средств и жизненным циклом валидаторов.",
    version="2.0.0",
    lifespan=lifespan,
)


class ErrorResponse(BaseModel):
    detail: str = PydanticField(..., examples=["Объект не найден"])


class VehicleCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "bort_number": "К404РТ77",
                    "route_number": "М2",
                    "model": "ЛиАЗ-5292.65",
                    "is_active": True,
                }
            ]
        }
    )

    bort_number: str = PydanticField(..., min_length=2, max_length=32, examples=["К404РТ77"], description="Уникальный бортовой номер ТС")
    route_number: str = PydanticField(..., min_length=1, max_length=32, examples=["М2"], description="Номер маршрута")
    model: str = PydanticField(..., min_length=2, max_length=64, examples=["ЛиАЗ-5292.65"], description="Модель автобуса/электробуса")
    is_active: bool = PydanticField(default=True, examples=[True], description="Выходит ли ТС на линию")


class VehicleUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "route_number": "Т88",
                    "is_active": False,
                }
            ]
        }
    )

    bort_number: str | None = PydanticField(default=None, min_length=2, max_length=32, examples=["К404РТ77"])
    route_number: str | None = PydanticField(default=None, min_length=1, max_length=32, examples=["Т88"])
    model: str | None = PydanticField(default=None, min_length=2, max_length=64, examples=["ЛиАЗ-5292.65"])
    is_active: bool | None = PydanticField(default=None, examples=[False])


class VehicleRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "bort_number": "А101ВС77",
                    "route_number": "М15",
                    "model": "ЛиАЗ-5292",
                    "is_active": True,
                    "created_at": "2026-03-10T09:00:00Z",
                }
            ]
        },
    )

    id: int
    bort_number: str
    route_number: str
    model: str
    is_active: bool
    created_at: datetime


class VehicleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bort_number: str
    route_number: str
    model: str
    is_active: bool


class ValidatorCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "serial_number": "VAL-2026-0010",
                    "model": "Validator Pro X2",
                    "mac_address": "AA:BB:CC:11:22:10",
                    "manufacture_date": "2026-03-01",
                    "firmware_version": "2.1.0",
                    "vehicle_id": None,
                }
            ]
        }
    )

    serial_number: str = PydanticField(..., min_length=3, max_length=64, examples=["VAL-2026-0010"])
    model: str = PydanticField(..., min_length=2, max_length=64, examples=["Validator Pro X2"])
    mac_address: str = PydanticField(..., min_length=12, max_length=32, examples=["AA:BB:CC:11:22:10"])
    manufacture_date: date = PydanticField(..., examples=["2026-03-01"])
    firmware_version: str = PydanticField(default="1.0.0", max_length=32, examples=["2.1.0"])
    vehicle_id: int | None = PydanticField(default=None, gt=0, examples=[None], description="ID ТС (если устанавливается сразу)")


class ValidatorUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "firmware_version": "2.2.5",
                    "status": "На складе",
                }
            ]
        }
    )

    model: str | None = PydanticField(default=None, min_length=2, max_length=64, examples=["Validator Pro X2"])
    firmware_version: str | None = PydanticField(default=None, min_length=1, max_length=32, examples=["2.2.5"])
    status: ValidatorStatus | None = PydanticField(default=None, examples=["На складе"])


class ValidatorBindRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"vehicle_id": 1}
            ]
        }
    )

    vehicle_id: int = PydanticField(..., gt=0, examples=[1], description="ID активного транспортного средства")


class ValidatorRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "serial_number": "VAL-2026-0001",
                    "model": "Validator Pro X2",
                    "mac_address": "AA:BB:CC:11:22:01",
                    "manufacture_date": "2026-01-10",
                    "firmware_version": "2.1.0",
                    "status": "На линии",
                    "vehicle_id": 1,
                    "vehicle": {
                        "id": 1,
                        "bort_number": "А101ВС77",
                        "route_number": "М15",
                        "model": "ЛиАЗ-5292",
                        "is_active": True,
                    },
                    "dismount_reason": None,
                    "created_at": "2026-01-10T10:00:00Z",
                    "updated_at": "2026-01-10T10:00:00Z",
                }
            ]
        },
    )

    id: int
    serial_number: str
    model: str
    mac_address: str
    manufacture_date: date
    firmware_version: str
    status: ValidatorStatus
    vehicle_id: int | None
    vehicle: VehicleSummary | None = None
    dismount_reason: str | None
    created_at: datetime
    updated_at: datetime


# ======================= ТРАНСПОРТНЫЕ СРЕДСТВА (ТС) =======================

@app.get(
    "/vehicles",
    tags=["Транспорт (ТС)"],
    summary="Список транспортных средств",
    response_model=list[VehicleRead],
)
def list_vehicles(
    is_active: bool | None = Query(default=None, description="Фильтр по активности ТС"),
    session: Session = Depends(get_session),
):
    query = select(Vehicle).order_by(Vehicle.id)
    if is_active is not None:
        query = query.where(Vehicle.is_active == is_active)
    return session.exec(query).all()


@app.post(
    "/vehicles",
    tags=["Транспорт (ТС)"],
    summary="Добавить новое ТС",
    status_code=status.HTTP_201_CREATED,
    response_model=VehicleRead,
    responses={409: {"model": ErrorResponse, "description": "ТС с таким бортовым номером уже существует"}},
)
def create_vehicle(data: VehicleCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(Vehicle).where(Vehicle.bort_number == data.bort_number)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ТС с таким бортовым номером уже существует",
        )

    vehicle = Vehicle(**data.model_dump())
    session.add(vehicle)
    session.commit()
    session.refresh(vehicle)
    return vehicle


@app.get(
    "/vehicles/{vehicle_id}",
    tags=["Транспорт (ТС)"],
    summary="Получить карточку ТС",
    response_model=VehicleRead,
    responses={404: {"model": ErrorResponse, "description": "ТС не найдено"}},
)
def get_vehicle(vehicle_id: int, session: Session = Depends(get_session)):
    vehicle = session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ТС не найдено")
    return vehicle


@app.patch(
    "/vehicles/{vehicle_id}",
    tags=["Транспорт (ТС)"],
    summary="Обновить данные ТС",
    response_model=VehicleRead,
    responses={
        404: {"model": ErrorResponse, "description": "ТС не найдено"},
        409: {"model": ErrorResponse, "description": "Бортовой номер уже занят"},
    },
)
def update_vehicle(vehicle_id: int, data: VehicleUpdate, session: Session = Depends(get_session)):
    vehicle = session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ТС не найдено")

    updates = data.model_dump(exclude_unset=True)
    if "bort_number" in updates and updates["bort_number"] != vehicle.bort_number:
        dup = session.exec(select(Vehicle).where(Vehicle.bort_number == updates["bort_number"])).first()
        if dup:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Бортовой номер уже занят")

    for field, value in updates.items():
        setattr(vehicle, field, value)

    session.add(vehicle)
    session.commit()
    session.refresh(vehicle)
    return vehicle


@app.delete(
    "/vehicles/{vehicle_id}",
    tags=["Транспорт (ТС)"],
    summary="Удалить ТС (валидаторы возвращаются на склад)",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse, "description": "ТС не найдено"}},
)
def delete_vehicle(vehicle_id: int, session: Session = Depends(get_session)):
    vehicle = session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ТС не найдено")

    attached_validators = session.exec(select(Validator).where(Validator.vehicle_id == vehicle_id)).all()
    for val in attached_validators:
        val.vehicle_id = None
        val.status = ValidatorStatus.IN_STOCK.value
        val.dismount_reason = "ТС удалено из реестра"
        val.updated_at = utc_now()
        session.add(val)

    session.delete(vehicle)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ======================= ВАЛИДАТОРЫ =======================

@app.get(
    "/validators",
    tags=["Валидаторы"],
    summary="Список валидаторов",
    response_model=list[ValidatorRead],
)
def list_validators(
    validator_status: ValidatorStatus | None = Query(default=None, alias="status", description="Фильтр по статусу"),
    vehicle_id: int | None = Query(default=None, gt=0, description="Фильтр по ID автобуса"),
    session: Session = Depends(get_session),
):
    query = select(Validator).order_by(Validator.id)
    if validator_status is not None:
        query = query.where(Validator.status == validator_status.value)
    if vehicle_id is not None:
        query = query.where(Validator.vehicle_id == vehicle_id)
    validators = session.exec(query).all()
    return [ValidatorRead.model_validate(v) for v in validators]


@app.post(
    "/validators",
    tags=["Валидаторы"],
    summary="Зарегистрировать новый валидатор",
    status_code=status.HTTP_201_CREATED,
    response_model=ValidatorRead,
    responses={
        400: {"model": ErrorResponse, "description": "Нельзя привязать к неактивному ТС"},
        404: {"model": ErrorResponse, "description": "Указанное ТС не найдено"},
        409: {"model": ErrorResponse, "description": "Дубликат серийного номера или MAC-адреса"},
    },
)
def register_validator(data: ValidatorCreate, session: Session = Depends(get_session)):
    if session.exec(select(Validator).where(Validator.serial_number == data.serial_number)).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Валидатор с таким серийным номером уже существует",
        )
    if session.exec(select(Validator).where(Validator.mac_address == data.mac_address)).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Валидатор с таким MAC-адресом уже существует",
        )

    initial_status = ValidatorStatus.IN_STOCK.value
    if data.vehicle_id is not None:
        vehicle = session.get(Vehicle, data.vehicle_id)
        if not vehicle:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Указанное ТС не найдено")
        if not vehicle.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ТС не активно и не может принимать валидаторы")
        initial_status = ValidatorStatus.ON_LINE.value

    validator = Validator(
        serial_number=data.serial_number,
        model=data.model,
        mac_address=data.mac_address,
        manufacture_date=data.manufacture_date,
        firmware_version=data.firmware_version,
        vehicle_id=data.vehicle_id,
        status=initial_status,
    )
    session.add(validator)
    session.commit()
    session.refresh(validator)
    return ValidatorRead.model_validate(validator)


@app.get(
    "/validators/{validator_id}",
    tags=["Валидаторы"],
    summary="Получить карточку валидатора",
    response_model=ValidatorRead,
    responses={404: {"model": ErrorResponse, "description": "Валидатор не найден"}},
)
def get_validator(validator_id: int, session: Session = Depends(get_session)):
    validator = session.get(Validator, validator_id)
    if not validator:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден")
    return ValidatorRead.model_validate(validator)


@app.patch(
    "/validators/{validator_id}",
    tags=["Валидаторы"],
    summary="Обновить параметры или статус валидатора",
    description="Позволяет обновить прошивку, модель или вернуть валидатор из ремонта ('В ремонте' -> 'На складе') / списать.",
    response_model=ValidatorRead,
    responses={
        400: {"model": ErrorResponse, "description": "Недопустимый переход статуса"},
        404: {"model": ErrorResponse, "description": "Валидатор не найден"},
    },
)
def update_validator(validator_id: int, data: ValidatorUpdate, session: Session = Depends(get_session)):
    validator = session.get(Validator, validator_id)
    if not validator:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден")

    updates = data.model_dump(exclude_unset=True)
    new_status: ValidatorStatus | None = updates.pop("status", None)

    if new_status is not None:
        if new_status == ValidatorStatus.ON_LINE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Для перевода в статус 'На линии' привяжите валидатор к ТС через PUT /validators/{id}/vehicle",
            )
        if validator.status == ValidatorStatus.ON_LINE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя сменить статус, пока валидатор на линии. Сначала снимите его через DELETE /validators/{id}/vehicle",
            )
        validator.status = new_status.value
        if new_status == ValidatorStatus.IN_STOCK:
            validator.dismount_reason = None

    for field, value in updates.items():
        setattr(validator, field, value)

    validator.updated_at = utc_now()
    session.add(validator)
    session.commit()
    session.refresh(validator)
    return ValidatorRead.model_validate(validator)


@app.put(
    "/validators/{validator_id}/vehicle",
    tags=["Валидаторы"],
    summary="Привязать валидатор к ТС",
    description="Переводит валидатор со склада на линию и закрепляет за выбранным автобусом.",
    response_model=ValidatorRead,
    responses={
        400: {"model": ErrorResponse, "description": "Неверный статус валидатора или неактивное ТС"},
        404: {"model": ErrorResponse, "description": "Валидатор или ТС не найдены"},
    },
)
def bind_validator_to_vehicle(
    validator_id: int,
    data: ValidatorBindRequest,
    session: Session = Depends(get_session),
):
    validator = session.get(Validator, validator_id)
    if not validator:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден")
    if validator.status != ValidatorStatus.IN_STOCK.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Привязать можно только валидатор в статусе 'На складе' (текущий: '{validator.status}')",
        )

    vehicle = session.get(Vehicle, data.vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ТС не найдено")
    if not vehicle.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ТС не активно и не может быть привязано")

    validator.vehicle_id = vehicle.id
    validator.dismount_reason = None
    validator.status = ValidatorStatus.ON_LINE.value
    validator.updated_at = utc_now()
    session.add(validator)
    session.commit()
    session.refresh(validator)
    return ValidatorRead.model_validate(validator)


@app.delete(
    "/validators/{validator_id}/vehicle",
    tags=["Валидаторы"],
    summary="Снять валидатор с ТС (отправить в ремонт)",
    response_model=ValidatorRead,
    responses={
        400: {"model": ErrorResponse, "description": "Валидатор не находится на линии"},
        404: {"model": ErrorResponse, "description": "Валидатор не найден"},
    },
)
def dismount_validator_from_vehicle(
    validator_id: int,
    reason: str = Query(
        default="Плановое снятие",
        min_length=2,
        max_length=255,
        examples=["Перегрев блока питания"],
        description="Причина демонтажа",
    ),
    session: Session = Depends(get_session),
):
    validator = session.get(Validator, validator_id)
    if not validator:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден")
    if validator.status != ValidatorStatus.ON_LINE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Снять с ТС можно только валидатор в статусе 'На линии'",
        )

    validator.vehicle_id = None
    validator.dismount_reason = reason
    validator.status = ValidatorStatus.IN_REPAIR.value
    validator.updated_at = utc_now()
    session.add(validator)
    session.commit()
    session.refresh(validator)
    return ValidatorRead.model_validate(validator)


@app.delete(
    "/validators/{validator_id}",
    tags=["Валидаторы"],
    summary="Удалить валидатор из реестра",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Сначала снимите валидатор с ТС"},
        404: {"model": ErrorResponse, "description": "Валидатор не найден"},
    },
)
def delete_validator(validator_id: int, session: Session = Depends(get_session)):
    validator = session.get(Validator, validator_id)
    if not validator:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Валидатор не найден")
    if validator.status == ValidatorStatus.ON_LINE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить валидатор, пока он установлен на ТС",
        )

    session.delete(validator)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)