"""Tek seferlik giris v2: kodu ister, sen repoya code.txt ekleyince girisi tamamlar."""
import asyncio
import json
import os
import subprocess
import time

from roborock.web_api import RoborockApiClient

EMAIL = os.environ["RR_EMAIL"]
WAIT_MINUTES = 10


def read_code() -> str:
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True)
    r = subprocess.run(
        ["git", "show", "origin/main:code.txt"], capture_output=True, text=True
    )
    if r.returncode == 0:
        return r.stdout.strip()
    return ""


async def main() -> None:
    api = RoborockApiClient(username=EMAIL)
    await api.request_code()
    print("Dogrulama kodu e-postana gonderildi.")
    print("SIMDI: baska bir sekmede repoya 'code.txt' adinda dosya ekle,")
    print("icine SADECE kodu yaz ve Commit et. Ben burada bekliyorum...")
    deadline = time.time() + WAIT_MINUTES * 60
    code = ""
    while time.time() < deadline:
        code = read_code()
        if code:
            print("code.txt bulundu, giris deneniyor...")
            break
        await asyncio.sleep(15)
    if not code:
        print("Sure doldu, code.txt gelmedi. Workflow'u bastan calistir.")
        raise SystemExit(1)
    user_data = await api.code_login(code)
    print("GIRIS BASARILI. Asagidaki tek satiri kopyala ve")
    print("RR_USER_DATA adinda secret olarak kaydet:")
    print(json.dumps(user_data.as_dict()))
    print("Sonra code.txt dosyasini repodan SIL.")


asyncio.run(main())
