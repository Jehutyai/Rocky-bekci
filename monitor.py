"""Rocky Bekci v2: %40'ta gorevi duraklatip sarja yollar, %100'de kaldigi yerden devam ettirir."""
import asyncio
import os

import requests
from roborock import RoborockCommand
from roborock.containers import DeviceData
from roborock.version_1_apis.roborock_mqtt_client_v1 import RoborockMqttClientV1
from roborock.web_api import RoborockApiClient

EMAIL = os.environ["RR_EMAIL"]
PASSWORD = os.environ["RR_PASSWORD"]
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
THRESHOLD = int(os.environ.get("BATTERY_THRESHOLD", "40"))
RESUME_AT = int(os.environ.get("RESUME_THRESHOLD", "100"))

# Roborock durum kodlari
ACTIVE_STATES = {4, 5, 7, 10, 11, 16, 17, 18}  # temizlikte / duraklatildi / hedefe gidiyor
RETURNING_STATES = {6, 15}                      # dock'a donuyor / kenetleniyor
CHARGING_STATES = {8, 100}                      # sarj oluyor / sarj tamam


def ntfy(msg: str, urgent: bool = False) -> None:
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode("utf-8"),
            headers={
                "Title": "Rocky Bekci",
                "Priority": "urgent" if urgent else "default",
                "Tags": "rotating_light" if urgent else "robot",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"ntfy gonderilemedi: {e}")


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
        ntfy(f"{device.name} cevap vermiyor. Ortada kalmis olabilir!", urgent=True)
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

    if error != 0:
        ntfy(f"{device.name} HATA verdi (kod {error}), pil %{battery}!", urgent=True)

    if battery is None or state is None:
        return

    if battery <= THRESHOLD and state in ACTIVE_STATES:
        print(f"Pil %{battery} <= %{THRESHOLD}: gorev duraklatilip sarja gonderiliyor")
        await send(user_data, device, model, RoborockCommand.APP_PAUSE)
        await asyncio.sleep(3)
        await send(user_data, device, model, RoborockCommand.APP_CHARGE)
        ntfy(
            f"{device.name} pil %{battery}: gorevi duraklattim, sarja yolladim. "
            f"%{RESUME_AT} olunca kaldigi yerden devam ettirecegim."
        )
    elif state in CHARGING_STATES and in_cleaning != 0 and battery >= RESUME_AT:
        print(f"Sarj %{battery} >= %{RESUME_AT} ve bekleyen gorev var: devam komutu gonderiliyor")
        await send(user_data, device, model, RoborockCommand.APP_START)
        ntfy(f"{device.name} tam sarj oldu, temizlige kaldigi yerden devam ediyor.")
    elif battery <= 25 and state in RETURNING_STATES:
        ntfy(
            f"{device.name} dock'a donuyor ama pil %{battery}. Varamayabilir!",
            urgent=True,
        )


async def main() -> None:
    web_api = RoborockApiClient(username=EMAIL)
    try:
        user_data = await web_api.pass_login(PASSWORD)
    except Exception as e:
        print(
            "Roborock girisi basarisiz. E-posta/sifre dogru mu? "
            "Google/Apple ile kayit olduysan once Roborock uygulamasindan hesabina sifre tanimla. "
            f"Hata: {e}"
        )
        raise

    # Hesap API surum farklarina karsi: once v3 dene, olmazsa v2
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
        print("Hesapta cihaz bulunamadi. Roborock hesabi dogru mu?")
        return
    for device in all_devices:
        model = models.get(device.product_id)
        if model:
            await check_device(user_data, device, model)


if __name__ == "__main__":
    asyncio.run(main())
