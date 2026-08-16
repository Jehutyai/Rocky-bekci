"""Rocky Bekci v4.6: gorevi izler, esikte KAPSAMA analiziyle kalan odalari + ayarlari
kaydeder, robotu sarja yollar, %100'de ayni ayarlarla kalanlardan devam eder."""
import asyncio
import json
import os
import subprocess
import time

from roborock import RoborockCommand
from roborock.containers import DeviceData, UserData
from roborock.version_1_apis.roborock_mqtt_client_v1 import RoborockMqttClientV1
from roborock.web_api import RoborockApiClient

EMAIL = os.environ["RR_EMAIL"]
USER_DATA_JSON = os.environ.get("RR_USER_DATA", "").strip()
THRESHOLD = int(os.environ.get("BATTERY_THRESHOLD", "45"))
RESUME_AT = int(os.environ.get("RESUME_THRESHOLD", "100"))
REPEAT = int(os.environ.get("RESUME_REPEAT", "2"))
DOLULUK_ESIK = int(os.environ.get("COVERAGE_THRESHOLD", "60"))
NOKTA_TABANI = int(os.environ.get("POINT_THRESHOLD", "100"))
FLAG = "kalan-gorev.json"

ACTIVE_STATES = {4, 5, 7, 10, 11, 16, 17, 18}
RETURNING_STATES = {6, 15}
CHARGING_STATES = {8, 100}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def push_repo(msg):
    git("config", "user.name", "rocky-bekci")
    git("config", "user.email", "actions@github.com")
    git("add", "-A")
    if git("commit", "-m", msg).returncode == 0:
        p = git("push")
        if p.returncode != 0:
            print(f"push hatasi: {p.stderr}")


def load_flag():
    if os.path.exists(FLAG):
        try:
            with open(FLAG) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_flag(data, msg):
    with open(FLAG, "w") as f:
        json.dump(data, f)
    push_repo(msg)


def clear_flag(msg):
    if os.path.exists(FLAG):
        os.remove(FLAG)
        push_repo(msg)


def snapshot_settings(status):
    ayar = {}
    for alan in ("fan_power", "mop_mode", "water_box_mode"):
        deger = getattr(status, alan, None)
        if isinstance(deger, int):
            ayar[alan] = deger
    return ayar


def parse_remaining(raw_map, candidates=None):
    from vacuum_map_parser_base.config.color import ColorsPalette
    from vacuum_map_parser_base.config.image_config import ImageConfig
    from vacuum_map_parser_base.config.size import Sizes
    from vacuum_map_parser_roborock.map_data_parser import RoborockMapDataParser

    parser = RoborockMapDataParser(ColorsPalette(), Sizes(), [], ImageConfig(), [])
    map_data = parser.parse(raw_map)
    rooms = map_data.rooms or {}
    if not rooms:
        print("haritada oda bulunamadi")
        return None
    points = []
    path = getattr(map_data, "path", None)
    if path is not None and getattr(path, "path", None):
        for parca in path.path:
            points.extend(parca)
    if len(points) < 10:
        print("haritada yol verisi yok/az")
        return None

    kutular = {}
    for sid, r in rooms.items():
        try:
            x0, x1 = sorted((r.x0, r.x1))
            y0, y1 = sorted((r.y0, r.y1))
            kutular[int(sid)] = (x0, y0, x1, y1)
        except Exception:
            continue
    if not kutular:
        print("oda kutulari okunamadi")
        return None

    def oda_bul(pt):
        adaylar = []
        for sid, (x0, y0, x1, y1) in kutular.items():
            if x0 <= pt.x <= x1 and y0 <= pt.y <= y1:
                adaylar.append(((x1 - x0) * (y1 - y0), sid))
        return min(adaylar)[1] if adaylar else None

    oda_noktalari = {sid: [] for sid in kutular}
    sira = []
    for pt in points:
        sid = oda_bul(pt)
        if sid is not None:
            oda_noktalari[sid].append(pt)
            if not sira or sira[-1] != sid:
                sira.append(sid)
    son_oda = sira[-1] if sira else None

    biten = set()
    for sid, pts in oda_noktalari.items():
        if not pts:
            print(f"oda {sid}: 0 nokta, doluluk %0 -> girilmemis")
            continue
        x0, y0, x1, y1 = kutular[sid]
        w = max(x1 - x0, 1)
        h = max(y1 - y0, 1)
        hucreler = set()
        for p in pts:
            gx = min(9, max(0, int((p.x - x0) * 10 / w)))
            gy = min(9, max(0, int((p.y - y0) * 10 / h)))
            hucreler.add((gx, gy))
        doluluk = len(hucreler)
        temiz = len(pts) >= NOKTA_TABANI and doluluk >= DOLULUK_ESIK and sid != son_oda
        etiket = "BITTI" if temiz else ("son/yarim" if sid == son_oda else "gezinti")
        print(f"oda {sid}: {len(pts)} nokta, doluluk %{doluluk} -> {etiket}")
        if temiz:
            biten.add(sid)

    tum = list(kutular.keys())
    havuz = [int(c) for c in candidates] if candidates else tum
    if candidates and any(b not in {int(c) for c in candidates} for b in biten):
        print("eski liste disinda oda taranmis: yeni gorev sayiliyor, havuz sifirlandi")
        havuz = tum
    kalan = [s for s in havuz if s not in biten]
    print(f"oda analizi -> biten: {sorted(biten)} | son/yarim: {son_oda} | kalan: {kalan}")
    return kalan


async def release(client):
    try:
        await client.async_release()
    except Exception:
        pass


async def send(user_data, device, model, command, params=None):
    client = RoborockMqttClientV1(user_data, DeviceData(device, model))
    try:
        if params is None:
            await client.send_command(command)
        else:
            await client.send_command(command, params)
    finally:
        await release(client)


async def restore_settings(user_data, device, model, ayar):
    esleme = {
        "fan_power": RoborockCommand.SET_CUSTOM_MODE,
        "mop_mode": RoborockCommand.SET_MOP_MODE,
        "water_box_mode": RoborockCommand.SET_WATER_BOX_CUSTOM_MODE,
    }
    for alan, komut in esleme.items():
        if alan in ayar:
            try:
                await send(user_data, device, model, komut, [int(ayar[alan])])
                print(f"ayar geri yuklendi: {alan}={ayar[alan]}")
            except Exception as e:
                print(f"ayar geri yuklenemedi ({alan}): {e}")


async def start_remaining(user_data, device, model, flag):
    kalan = [int(s) for s in flag.get("kalan") or []]
    await restore_settings(user_data, device, model, flag.get("ayar") or {})
    await send(
        user_data, device, model,
        RoborockCommand.APP_SEGMENT_CLEAN,
        [{"segments": kalan, "repeat": REPEAT}],
    )
    print(f"kalan odalar baslatildi: {kalan} (x{REPEAT})")


async def fetch_map(user_data, device, model):
    client = RoborockMqttClientV1(user_data, DeviceData(device, model))
    try:
        return await client.get_map_v1()
    finally:
        await release(client)


async def analyze(user_data, device, model, candidates):
    try:
        raw = await fetch_map(user_data, device, model)
        if raw:
            return parse_remaining(raw, candidates)
    except Exception as e:
        print(f"harita okunamadi: {e}")
    return None


async def check_device(user_data, device, model):
    client = RoborockMqttClientV1(user_data, DeviceData(device, model))
    try:
        status = await client.get_status()
    except Exception as e:
        print(f"{device.name}: duruma ulasilamadi ({e})")
        return
    finally:
        await release(client)

    if status is None:
        print(f"{device.name}: durum bos geldi")
        return

    battery = status.battery
    state = status.state
    error = getattr(status, "error_code", 0) or 0
    in_cleaning = getattr(status, "in_cleaning", 0) or 0
    flag = load_flag()
    simdi = int(time.time())
    kayit = "beklemede" if flag.get("beklemede") else ("devam" if flag.get("devam") else "yok")
    print(
        f"{device.name}: pil %{battery} | durum {state} | hata {error} "
        f"| gorev {in_cleaning} | kayit {kayit}"
    )
    if battery is None or state is None:
        return

    if battery <= THRESHOLD and state in ACTIVE_STATES:
        print("Esik altinda: analiz + eve gonderme")
        ayar = flag.get("ayar") or snapshot_settings(status)
        candidates = flag.get("kalan")
        kalan = await analyze(user_data, device, model, candidates)
        if kalan is None and candidates:
            kalan = [int(c) for c in candidates]
            print(f"harita cozulmedi, onceki liste korunuyor: {kalan}")
        await send(user_data, device, model, RoborockCommand.APP_PAUSE)
        await asyncio.sleep(3)
        await send(user_data, device, model, RoborockCommand.APP_CHARGE)
        save_flag(
            {"beklemede": True, "kalan": kalan, "ayar": ayar, "t": simdi},
            "bekci: gorev kesildi",
        )
    elif flag.get("beklemede") and state in CHARGING_STATES and battery >= RESUME_AT:
        if flag.get("kalan"):
            yeni = {
                "devam": True, "kalan": flag["kalan"], "ayar": flag.get("ayar"),
                "basladi": False, "deneme": 1, "t": simdi,
            }
            await start_remaining(user_data, device, model, yeni)
            save_flag(yeni, "bekci: kalan odalar baslatildi")
        else:
            print("Kalan oda listesi yok; robot dockta guvende, kayit kapatiliyor.")
            clear_flag("bekci: kalan belirlenemedi")
    elif flag.get("devam") and state in ACTIVE_STATES and not flag.get("basladi"):
        flag["basladi"] = True
        save_flag(flag, "bekci: devam dogrulandi")
    elif (
        flag.get("devam") and not flag.get("basladi")
        and state in CHARGING_STATES and battery >= RESUME_AT
        and simdi - int(flag.get("t", 0)) > 600
    ):
        if int(flag.get("deneme", 1)) < 3:
            print("Devam teyit edilemedi: kor tekrar yerine harita analiz ediliyor")
            kalan = await analyze(user_data, device, model, flag.get("kalan"))
            if kalan == []:
                print("Analiz: her sey bitmis, kayit kapatiliyor")
                clear_flag("bekci: is tamamlandi (analizle dogrulandi)")
            else:
                if kalan:
                    flag["kalan"] = kalan
                flag["deneme"] = int(flag.get("deneme", 1)) + 1
                flag["t"] = simdi
                await start_remaining(user_data, device, model, flag)
                save_flag(flag, "bekci: devam tekrar denendi")
        else:
            print("Devam 3 kez tutmadi; robot dockta guvende, kayit kapatiliyor.")
            clear_flag("bekci: devam ettirilemedi")
    elif (
        flag.get("devam") and flag.get("basladi")
        and state in CHARGING_STATES and in_cleaning == 0
        and simdi - int(flag.get("t", 0)) > 900
    ):
        print("Devam etabi kesintisiz bitti: ev tamam, kayit kapatiliyor")
        clear_flag("bekci: is tamamlandi")
    elif flag.get("beklemede") and state in ACTIVE_STATES and battery > THRESHOLD:
        print("Kullanici yeni gorev baslatmis, eski kayit iptal")
        clear_flag("bekci: kullanici devraldi")
    elif battery <= 25 and state in RETURNING_STATES:
        print(f"DIKKAT: donuyor ama pil %{battery}")


async def main():
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
    for device in all_devices:
        model = models.get(device.product_id)
        if model:
            await check_device(user_data, device, model)


if __name__ == "__main__":
    asyncio.run(main())
