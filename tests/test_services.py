import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import api_gateway.main as gw_main
import device_registry.main as dr_main
import incident_analytics.main as ia_main
import shared.db as shared_db
import telemetry_service.main as ts_main


@pytest.fixture(name="clients")
def clients_fixture(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        with Session(test_engine) as session:
            yield session

    dr_main.app.dependency_overrides[shared_db.get_session] = get_test_session
    ts_main.app.dependency_overrides[shared_db.get_session] = get_test_session
    ia_main.app.dependency_overrides[shared_db.get_session] = get_test_session

    dr_client = TestClient(dr_main.app)
    ts_client = TestClient(ts_main.app)
    ia_client = TestClient(ia_main.app)

    def fake_get(url, timeout=3.0):
        if "/validators/" in url:
            v_id = url.rsplit("/validators/", 1)[-1]
            return dr_client.get(f"/validators/{v_id}")
        raise AssertionError(f"Unexpected GET URL: {url}")

    def fake_post(url, json, timeout=3.0):
        if url.endswith("/incidents"):
            return ia_client.post("/incidents", json=json)
        raise AssertionError(f"Unexpected POST URL: {url}")

    monkeypatch.setattr(ts_main.httpx, "get", fake_get)
    monkeypatch.setattr(ts_main.httpx, "post", fake_post)

    yield dr_client, ts_client, ia_client

    dr_main.app.dependency_overrides.clear()
    ts_main.app.dependency_overrides.clear()
    ia_main.app.dependency_overrides.clear()


def test_vehicle_and_validator_lifecycle(clients):
    dr_client, _, _ = clients

    # 1. Создание ТС
    veh = dr_client.post("/vehicles", json={
        "bort_number": "А100ВС77",
        "route_number": "М15",
        "model": "ЛиАЗ-5292",
        "is_active": True,
    })
    assert veh.status_code == 201
    veh_id = veh.json()["id"]

    # Дубликат ТС -> 409
    dup_veh = dr_client.post("/vehicles", json={
        "bort_number": "А100ВС77",
        "route_number": "М15",
        "model": "ЛиАЗ-5292",
    })
    assert dup_veh.status_code == 409

    # 2. Регистрация валидатора
    val = dr_client.post("/validators", json={
        "serial_number": "SN-100",
        "model": "Validator Pro X2",
        "mac_address": "AA:BB:CC:11:22:33",
        "manufacture_date": "2026-01-10",
    })
    assert val.status_code == 201
    assert val.json()["status"] == "На складе"
    v_id = val.json()["id"]

    # 3. Привязка к неактивному ТС -> 400
    inactive_veh = dr_client.post("/vehicles", json={
        "bort_number": "Х999ХХ77",
        "route_number": "900",
        "model": "НефАЗ",
        "is_active": False,
    }).json()
    bad_bind = dr_client.put(f"/validators/{v_id}/vehicle", json={"vehicle_id": inactive_veh["id"]})
    assert bad_bind.status_code == 400

    # 4. Успешная привязка к активному ТС (PUT)
    good_bind = dr_client.put(f"/validators/{v_id}/vehicle", json={"vehicle_id": veh_id})
    assert good_bind.status_code == 200
    assert good_bind.json()["status"] == "На линии"
    assert good_bind.json()["vehicle"]["bort_number"] == "А100ВС77"

    # Нельзя удалить валидатор, пока он на линии -> 400
    assert dr_client.delete(f"/validators/{v_id}").status_code == 400

    # 5. Снятие с ТС в ремонт (DELETE /vehicle)
    dismount = dr_client.delete(f"/validators/{v_id}/vehicle", params={"reason": "поломка кулера"})
    assert dismount.status_code == 200
    assert dismount.json()["status"] == "В ремонте"
    assert dismount.json()["vehicle_id"] is None

    # 6. Возврат из ремонта на склад и обновление прошивки (PATCH)
    repaired = dr_client.patch(f"/validators/{v_id}", json={
        "firmware_version": "2.2.0",
        "status": "На складе",
    })
    assert repaired.status_code == 200
    assert repaired.json()["status"] == "На складе"
    assert repaired.json()["firmware_version"] == "2.2.0"

    # 7. Удаление валидатора (DELETE -> 204)
    assert dr_client.delete(f"/validators/{v_id}").status_code == 204
    assert dr_client.get(f"/validators/{v_id}").status_code == 404


def test_telemetry_incidents_notifications_and_cascade(clients):
    dr_client, ts_client, ia_client = clients

    v_id = dr_client.post("/validators", json={
        "serial_number": "SN-200",
        "model": "Validator Pro X2",
        "mac_address": "AA:BB:CC:11:22:44",
        "manufacture_date": "2026-02-01",
    }).json()["id"]

    # Телеметрия для несуществующего валидатора -> 404
    missing_resp = ts_client.post("/telemetry", json={
        "validator_id": 9999,
        "temperature": 45.0,
        "voltage": 12.5,
        "nfc_is_working": True,
    })
    assert missing_resp.status_code == 404

    # 1. Нормальная телеметрия
    ok_resp = ts_client.post("/telemetry", json={
        "validator_id": v_id,
        "temperature": 45.0,
        "voltage": 12.5,
        "nfc_is_working": True,
    })
    assert ok_resp.status_code == 201
    assert ok_resp.json()["incident_triggered"] is False

    status_resp = ts_client.get(f"/telemetry/status/{v_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "Online"

    # 2. Перегрев -> создаётся инцидент со ссылкой на telemetry_id и уведомление
    fail_resp = ts_client.post("/telemetry", json={
        "validator_id": v_id,
        "temperature": 88.5,
        "voltage": 12.4,
        "nfc_is_working": True,
    })
    assert fail_resp.status_code == 201
    assert fail_resp.json()["incident_triggered"] is True
    inc_id = fail_resp.json()["incident_id"]
    tel_id = fail_resp.json()["telemetry"]["id"]

    # Повторный перегрев при активном инциденте не создаёт дубликат
    dup_fail = ts_client.post("/telemetry", json={
        "validator_id": v_id,
        "temperature": 90.0,
        "voltage": 12.4,
        "nfc_is_working": True,
    })
    assert dup_fail.status_code == 201
    assert dup_fail.json()["incident_triggered"] is False

    # Детальная карточка инцидента содержит telemetry_id и вложенные уведомления
    detail = ia_client.get(f"/incidents/{inc_id}")
    assert detail.status_code == 200
    assert detail.json()["telemetry_id"] == tel_id
    assert len(detail.json()["notifications"]) == 1
    notif_id = detail.json()["notifications"][0]["id"]

    # 3. Отмечаем уведомление прочитанным + переводим инцидент в работу и закрываем (PATCH)
    assert ia_client.patch(f"/notifications/{notif_id}", json={"is_read": True}).json()["is_read"] is True
    assert ia_client.patch(f"/incidents/{inc_id}", json={"status": "В работе"}).json()["status"] == "В работе"
    closed = ia_client.patch(f"/incidents/{inc_id}", json={
        "status": "Закрыт",
        "engineer_comment": "Замена кулера",
    })
    assert closed.status_code == 200
    assert closed.json()["status"] == "Закрыт"
    assert closed.json()["closed_at"] is not None

    analytics = ia_client.get("/analytics").json()
    assert analytics["total_incidents"] == 1
    assert analytics["closed_incidents"] == 1
    assert analytics["unread_notifications"] == 0

    # 4. Каскадное удаление инцидента удаляет и уведомление
    assert ia_client.delete(f"/incidents/{inc_id}").status_code == 204
    assert ia_client.get("/notifications").json() == []


def test_gateway_openapi_aggregation(monkeypatch):
    gw_main.app.openapi_schema = None

    dr_spec = dr_main.app.openapi()
    ts_spec = ts_main.app.openapi()
    ia_spec = ia_main.app.openapi()

    class FakeResp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def fake_gw_get(url, timeout=2.0):
        if "device_registry" in url:
            return FakeResp(dr_spec)
        if "telemetry_service" in url:
            return FakeResp(ts_spec)
        if "incident_analytics" in url:
            return FakeResp(ia_spec)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(gw_main.httpx, "get", fake_gw_get)

    gw_client = TestClient(gw_main.app)
    openapi = gw_client.get("/openapi.json").json()

    assert "/vehicles" in openapi["paths"]
    assert "/validators" in openapi["paths"]
    assert "/telemetry" in openapi["paths"]
    assert "/incidents" in openapi["paths"]
    assert "/analytics" in openapi["paths"]