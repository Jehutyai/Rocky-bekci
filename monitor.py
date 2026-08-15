"""Rocky Bekci v3: %40'ta duraklatip sarja yollar, %100'de kaldigi yerden devam ettirir."""
import asyncio
import json
import os

from roborock import RoborockCommand
from roborock.containers import DeviceData, UserData
from roborock.version_1_apis.roborock_mqtt_client_v1 import RoborockMqttClientV1
from roborock.web_api import RoborockApiClient

EMAIL = os.environ["RR_EMAIL"]
USER_DATA_JSON = os.environ.get("RR_USER_DATA", "").strip()
THRESHOLD = int(os.environ.get("BATTERY_THRESHOLD", "40"))
RESUME_AT = int(os.environ.get("RESUME_THRESHOLD", "100"))

ACTIVE_STATES = {4, 5, 7, 10, 11, 16, 17, 18}
RETURNING_STATES = {6, 15}
CHARGING_STATES = {8, 100}


async def send(user_data, device, model, command) -> None:
    client = RoborockMqttClientV1(user_data, DeviceData(device, model))
    try:
        await client.send_command(command)
    finally:
        try:
            await client.async_release()
        except Exception:
            pass


async def check_device(user_data, device, model) -> None:
    client = RoborockMqttClientV1(user_data, DeviceData(device, model))
    try:
        status = await client.get_status()
    except Exception as e:
        print(f"{device.name}: duruma ulasilamadi ({e})")
        return
    finally:
        try:
            await client.async_release()
        except Exception:
            pass

    if status is None:
        print(f"{device.name}: durum bilgisi bos geldi")
        return

    battery = status.battery
    state = status.state
    error = getattr(status, "error_code", 0) or 0
    in_cleaning = getattr(status, "in_cleaning", 0) or 0
    print(
        f"{device.name}: pil %{battery} | durum {state} | hata {error} | bekleyen gorev {in_cleaning}"
    )

    if battery is None or state is None:
        return

    if battery <= THRESHOLD and state in ACTIVE_STATES:
        print(f"Pil %{battery} <= %{THRESHOLD}: gorev duraklatilip sarja gonderiliyor")
        await send(user_data, device, model, RoborockCommand.APP_PAUSE)
        await asyncio.sleep(3)
        await send(user_data, device, model, RoborockCommand.APP_CHARGE)
    elif state in CHARGING_STATES and in_cleaning != 0 and battery >= RESUME_AT:
        print(f"Sarj %{battery} >= %{RESUME_AT} ve bekleyen gorev var: devam komutu")
        await send(user_data, device, model, RoborockCommand.APP_START)
    elif battery <= 25 and state in RETURNING_STATES:
        print(f"DIKKAT: donuyor ama pil %{battery}")


async def main() -> None:
    if not USER_DATA_JSON:
        print("RR_USER_DATA secret'i eksik.")
        raise SystemExit(1)
    user_data = UserData.from_dict(json.loads(USER_DATA_JSON))
    web_api = RoborockApiClient(username=EMAIL)

    if hasattr(web_api, "get_home_data_v3"):
        try:
            home_data = await web_api.get_home_data_v3(user_data)
        except Exception:
            home_data = await web_api.get_home_data_v2(user_data)
    else:
        home_data = await web_api.get_home_data_v2(user_data)

    models = {p.id: p.model for p in home_data.products}
    all_devices = list(home_data.devices) + list(
        getattr(home_data, "received_devices", None) or []
    )
    if not all_devices:
        print("Hesapta cihaz bulunamadi.")
        return
    for device in all_devices:
        model = models.get(device.product_id)
        if model:
            await check_device(user_data, device, model)


if __name__ == "__main__":
    asyncio.run(main())
