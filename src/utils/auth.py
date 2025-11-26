# Stellt Funktionen zur Verfügung, um eingehende Anfragen zu authentifizieren
# main.py ruft Funktionen aus dieser Datei auf, bevor Logic aufgerufen wird
# -> Nicht jeder kann Requests schicken, nur Tradinfg View mit dem richtigen Secret

def verify(payload: dict):
    # prüft shared secret, token oder hash