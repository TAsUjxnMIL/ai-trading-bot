# Stellt Funktionen zur Verfügung, um eingehende Anfragen zu authentifizieren
# main.py ruft Funktionen aus dieser Datei auf, bevor Logic aufgerufen wird
# -> Nicht jeder kann Requests schicken, nur Tradinfg View mit dem richtigen Secret

# src/utils/auth.py
import os
from fastapi import HTTPException

SECRET = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "changeme")

def verify(payload: dict):
    if payload.get("secret") != SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret")