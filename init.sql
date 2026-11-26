CREATE TABLE IF NOT EXISTS vehicles (
    id SERIAL PRIMARY KEY,
    bort_number VARCHAR(32) NOT NULL UNIQUE,
    route_number VARCHAR(32) NOT NULL,
    model VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_vehicles_bort_number ON vehicles (bort_number);

CREATE TABLE IF NOT EXISTS validators (
    id SERIAL PRIMARY KEY,
    serial_number VARCHAR(64) NOT NULL UNIQUE,
    model VARCHAR(64) NOT NULL,
    mac_address VARCHAR(32) NOT NULL UNIQUE,
    manufacture_date DATE NOT NULL,
    firmware_version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    status VARCHAR(32) NOT NULL DEFAULT 'На складе',
    vehicle_id INTEGER NULL REFERENCES vehicles(id) ON DELETE SET NULL,
    dismount_reason VARCHAR(255) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_validators_serial_number ON validators (serial_number);
CREATE INDEX IF NOT EXISTS ix_validators_vehicle_id ON validators (vehicle_id);

CREATE TABLE IF NOT EXISTS telemetry (
    id SERIAL PRIMARY KEY,
    validator_id INTEGER NOT NULL REFERENCES validators(id) ON DELETE CASCADE,
    temperature DOUBLE PRECISION NOT NULL,
    voltage DOUBLE PRECISION NOT NULL,
    nfc_is_working BOOLEAN NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_telemetry_validator_id ON telemetry (validator_id);

CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    validator_id INTEGER NOT NULL REFERENCES validators(id) ON DELETE CASCADE,
    telemetry_id INTEGER NULL REFERENCES telemetry(id) ON DELETE SET NULL,
    error_type VARCHAR(255) NOT NULL,
    severity VARCHAR(32) NOT NULL DEFAULT 'Высокая',
    status VARCHAR(32) NOT NULL DEFAULT 'Открыт',
    engineer_comment TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ix_incidents_validator_id ON incidents (validator_id);
CREATE INDEX IF NOT EXISTS ix_incidents_telemetry_id ON incidents (telemetry_id);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    recipient VARCHAR(64) NOT NULL DEFAULT 'service_engineer',
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'delivered',
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_notifications_incident_id ON notifications (incident_id);

-- Стартовые данные для демонстрации на защите
INSERT INTO vehicles (bort_number, route_number, model, is_active) VALUES
    ('А101ВС77', 'М15', 'ЛиАЗ-5292', TRUE),
    ('В202ЕК77', 'Т73', 'КамАЗ-6282 (Электробус)', TRUE),
    ('С303МН77', '904', 'НефАЗ-5299', FALSE)
ON CONFLICT (bort_number) DO NOTHING;

INSERT INTO validators (serial_number, model, mac_address, manufacture_date, firmware_version, status, vehicle_id) VALUES
    ('VAL-2026-0001', 'Validator Pro X2', 'AA:BB:CC:11:22:01', '2026-01-10', '2.1.0', 'На линии',
        (SELECT id FROM vehicles WHERE bort_number = 'А101ВС77')),
    ('VAL-2026-0002', 'Validator Pro X2', 'AA:BB:CC:11:22:02', '2026-01-12', '2.1.0', 'На линии',
        (SELECT id FROM vehicles WHERE bort_number = 'В202ЕК77')),
    ('VAL-2026-0003', 'Validator Mini S1', 'AA:BB:CC:11:22:03', '2026-02-01', '1.4.2', 'На складе', NULL)
ON CONFLICT (serial_number) DO NOTHING;