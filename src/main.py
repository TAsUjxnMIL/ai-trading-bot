# Wir empfangen Webhook Anfragen von Trading View
# JSON Check -> Authentification passt? 
# JA ? JSON -> Business Logic / Trading Bot
"""
Verbindungen:
importiert config.py → um z. B. das Secret oder ENV zu kennen
importiert auth.py → prüft, ob der Request gültig ist
importiert signal_service.py → gibt den validierten Payload weiter
nutzt logger.py → loggt Requests/Fehler
"""

@router.post("/webhook/tradingview")
async def tradingview_webhook(request: Request):
    payload = await request.json()
    auth.verify(payload)
    signal_service.handle_signal(payload)
    return {"status": "ok"}
