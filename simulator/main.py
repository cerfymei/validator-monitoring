import os
import random
import time
import httpx

DEVICE_SERVICE_URL = os.getenv("DEVICE_SERVICE_URL", "http://device_registry:8000")
TELEMETRY_URL = os.getenv("TELEMETRY_URL", "http://telemetry_service:8000/telemetry")

print("[INFO] Simulator started. Waiting 5s for services...")
time.sleep(5)
print("[INFO] Starting telemetry loop.")

iteration = 0

while True:
    iteration += 1

    try:
        val_resp = httpx.get(
            f"{DEVICE_SERVICE_URL}/validators",
            params={"status": "На линии"},
            timeout=3.0,
        )
        val_resp.raise_for_status()
        active_validators = val_resp.json()
    except Exception as e:
        print(f"[WARN] Cannot fetch active validators yet: {e}")
        time.sleep(4)
        continue

    if not active_validators:
        print("[INFO] No validators with status 'На линии'. Waiting...")
        time.sleep(5)
        continue

    for idx, validator in enumerate(active_validators):
        v_id = validator["id"]
        temperature = round(random.uniform(40.0, 60.0), 1)
        voltage = round(random.uniform(12.2, 14.3), 1)
        nfc_working = True

        if iteration % 5 == 0:
            if idx % 2 == 0:
                temperature = round(random.uniform(78.0, 92.0), 1)
                print(f"[WARN] Injecting overheat fault: validator_id={v_id}, temp={temperature}C")
            else:
                nfc_working = False
                print(f"[WARN] Injecting NFC fault: validator_id={v_id}")

        packet = {
            "validator_id": v_id,
            "temperature": temperature,
            "voltage": voltage,
            "nfc_is_working": nfc_working,
        }

        try:
            response = httpx.post(TELEMETRY_URL, json=packet, timeout=3.0)
            data = response.json()
            state = "INCIDENT_TRIGGERED" if data.get("incident_triggered") else "OK"
            print(
                f"[SEND] validator_id={v_id} ({validator['serial_number']}) "
                f"temp={temperature} voltage={voltage} nfc={nfc_working} -> {state}"
            )
        except Exception as e:
            print(f"[ERROR] Failed to send telemetry: {e}")

    time.sleep(5)