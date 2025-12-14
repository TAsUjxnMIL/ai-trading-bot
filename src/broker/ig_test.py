import os
import requests

API_KEY   = os.getenv("IG_API_KEY")      or "4ede0f0a2e67109fa9f87ca5a92941e56ba87a28"
USERNAME  = os.getenv("IG_USERNAME")     or "sujan2000_demo"
PASSWORD  = os.getenv("IG_PASSWORD")     or "pengox-ryngu6-noskeT"

BASE_URL = "https://demo-api.ig.com/gateway/deal"

headers = {
    "X-IG-API-KEY": API_KEY,
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json; charset=UTF-8",
    "Version": "2",
}

data = {
    "identifier": USERNAME,
    "password": PASSWORD,
}

resp = requests.post(f"{BASE_URL}/session", headers=headers, json=data)

print("STATUS:", resp.status_code)
print("HEADERS:", dict(resp.headers))
print("BODY:", resp.text)


"""
Anfang 2000-3.5 = 
2000 -> TP 2036
2006 -> SL 2001, im Abstand von 5 
Erst ab 2.5+

2002.5 SL von -3.5 auf 



Am Anfang SL niedriger

Später wird SL wird höher gesetzt

MP = 2000 -> Wenn SL 3.5 ->1996.5

2001.5 -> SL immer nochj gleich 
Wenn jetzt noch höher 2002.5 1997.5 = SL 
"""