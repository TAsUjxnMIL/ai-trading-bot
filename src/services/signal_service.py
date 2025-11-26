# Controller zwischen Webhook und Business-Logik (trade_engine.py)
"""
Typische Aufgaben:
Webhook-Payload in ein internes Python-Objekt/Feldstruktur mappen.
Validierung: sind symbol, signal, timestamp sinnvoll?
Signal in der DB speichern.
trade_engine aufrufen, um zu entscheiden, ob gehandelt werden soll.

Verbindungen:
Aufgerufen von main.py
Verwendet models.py und db.py, um das Signal zu speichern
Ruft trade_engine.py auf
Nutzt logger.py für Logs
"""

def handle_signal(payload: dict):
    """
    handle_signal: Verarbeitet ein empfangenes Trading View Signal.
    - Nimmt Signal entgegen
    - Speichert in der DB
    - Ruft trade_engine auf, um zu handeln
    Args:
        payload (dict): Das empfangene Signal als Dictionary.
    """
    signal = models.Signal.from_payload(payload)
    db.save(signal)
    trade_engine.process_signal(signal)


def save_signal(signal: models.Signal):
    """
    Speichert das Signal in der Datenbank.
    Args:
        signal (models.Signal): Das zu speichernde Signal.
    """
    db.signals.save(signal)

def find_signal_by_external_id(external_id: str) -> models.Signal | None:
    """
    Sucht ein Signal in der DB anhand der externen ID.
    Args:
        external_id (str): Die externe ID des Signals.
    """
    ...