"""Tek seferlik giris: e-posta koduyla Roborock'tan kalici anahtar alir."""
import asyncio
import json
import os

from roborock.web_api import RoborockApiClient

EMAIL = os.environ["RR_EMAIL"]
CODE = os.environ.get("RR_CODE", "").strip()


async def main() -> None:
    api = RoborockApiClient(username=EMAIL)
    if not CODE:
        await api.request_code()
        print("Dogrulama kodu e-postana gonderildi (spam klasorune de bak).")
        print("Simdi ayni workflow'u, kod kutusuna gelen kodu yazarak TEKRAR calistir.")
        return
    user_data = await api.code_login(CODE)
    print("ASAGIDAKI TEK SATIRI KOPYALA ve RR_USER_DATA adinda secret olarak kaydet:")
    print(json.dumps(user_data.as_dict()))


asyncio.run(main())
