"""Persistencia y webhook de estados de WhatsApp; no reenvía mensajes."""
import hashlib
import hmac
import json
import logging
import os
import secrets

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from psycopg2.extras import RealDictCursor
from starlette.concurrency import run_in_threadpool

from database import get_db_connection
from whatsapp_results import summarize_results

router = APIRouter(tags=["WhatsApp"])
logger = logging.getLogger("uvicorn.error")
# La evidencia de entrega/lectura prevalece sobre un fallo. Un 'sent' tardío
# no borra un fallo; delivered/read sí confirman recepción del mensaje.
STATUS_RANK = {"sent": 1, "failed": 2, "delivered": 3, "read": 4}
SCHEMA = """
CREATE TABLE IF NOT EXISTS WhatsAppAlertAccess (
    alert_id INTEGER PRIMARY KEY REFERENCES Alerts(alert_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS WhatsAppNotifications (
    alert_id INTEGER NOT NULL REFERENCES Alerts(alert_id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    message_id TEXT,
    initial_status TEXT NOT NULL DEFAULT 'unknown',
    error_code TEXT,
    PRIMARY KEY (alert_id, contact_id)
);
CREATE INDEX IF NOT EXISTS whatsapp_notifications_message_idx
    ON WhatsAppNotifications(message_id);
CREATE TABLE IF NOT EXISTS WhatsAppDelivery (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    status_rank INTEGER NOT NULL,
    event_timestamp BIGINT NOT NULL,
    error_code TEXT
);
"""
UPSERT_DELIVERY = """
INSERT INTO WhatsAppDelivery(message_id, status, status_rank, event_timestamp, error_code)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (message_id) DO UPDATE SET
    status = EXCLUDED.status, status_rank = EXCLUDED.status_rank,
    event_timestamp = EXCLUDED.event_timestamp, error_code = EXCLUDED.error_code
WHERE EXCLUDED.status_rank > WhatsAppDelivery.status_rank
   OR (EXCLUDED.status_rank = WhatsAppDelivery.status_rank
       AND EXCLUDED.event_timestamp > WhatsAppDelivery.event_timestamp);
"""


def init_status_schema():
    """Migración aditiva e idempotente; se ejecuta al iniciar el backend."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(SCHEMA)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def prepare_tracking(cursor, alert_id, contacts):
    # Solo se almacena el hash; el secreto de consulta se entrega al creador.
    token = secrets.token_urlsafe(32)
    cursor.execute("INSERT INTO WhatsAppAlertAccess(alert_id, token_hash) VALUES (%s, %s)",
                   (alert_id, hashlib.sha256(token.encode()).hexdigest()))
    for contact in contacts:
        cursor.execute("""INSERT INTO WhatsAppNotifications(alert_id, contact_id, name)
                          VALUES (%s, %s, %s)""",
                       (alert_id, contact["contact_id"], contact["name"]))
    return token


def save_initial_results(cursor, alert_id, results):
    for item in results:
        code = item.get("error_code")
        cursor.execute("""UPDATE WhatsAppNotifications
                          SET message_id=%s, initial_status=%s, error_code=%s
                          WHERE alert_id=%s AND contact_id=%s""",
                       (item.get("message_id"), item["status"],
                        str(code) if code is not None else None,
                        alert_id, item["contact_id"]))


def valid_signature(raw, signature, secret):
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def extract_statuses(payload, phone_id):
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    events = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        raise ValueError("entry inválido")
    for entry in entries:
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            if value.get("metadata", {}).get("phone_number_id") != phone_id:
                continue
            for item in value.get("statuses", []):
                status = item.get("status")
                if status not in STATUS_RANK:
                    continue
                message_id = item.get("id")
                if not isinstance(message_id, str) or not message_id or len(message_id) > 2048:
                    raise ValueError("id inválido")
                timestamp = int(item["timestamp"])
                if not 0 <= timestamp <= 9223372036854775807:
                    raise ValueError("timestamp inválido")
                errors = item.get("errors") or []
                code = errors[0].get("code") if errors and isinstance(errors[0], dict) else None
                events.append((message_id, status, STATUS_RANK[status], timestamp,
                               str(code) if status == "failed" and isinstance(code, int) else None))
    return events


def persist_events(events):
    # Puede llegar un webhook antes de que termine POST /alerts: se guarda
    # por wamid y la consulta lo enlaza después, sin perder ese evento.
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(UPSERT_DELIVERY, events)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if not token:
        raise HTTPException(503, "Webhook no configurado")
    if (hub_mode == "subscribe" and hub_challenge is not None
            and hmac.compare_digest((hub_verify_token or "").encode(), token.encode())):
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(403, "Token de verificación inválido")


@router.post("/webhook")
async def receive_webhook(request: Request):
    secret = os.environ.get("META_APP_SECRET", "")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not secret or not phone_id:
        raise HTTPException(503, "Webhook no configurado")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1024 * 1024:
            raise HTTPException(413, "Webhook demasiado grande")
    if not valid_signature(bytes(raw), request.headers.get("x-hub-signature-256"), secret):
        raise HTTPException(403, "Firma inválida")
    try:
        events = extract_statuses(json.loads(raw), phone_id)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        raise HTTPException(400, "Payload inválido")
    if events:
        try:
            await run_in_threadpool(persist_events, events)
            logger.info("WhatsApp webhook persisted_statuses=%s", len(events))
        except Exception:
            # No confirmar recepción si no se pudo persistir: permite reintentos.
            logger.error("WhatsApp webhook storage_failed")
            raise HTTPException(503, "No se pudieron guardar los estados")
    return {"status": "received"}


@router.get("/alerts/{alert_id}/whatsapp")
def get_whatsapp_status(alert_id: int, response: Response,
                        x_alert_status_token: str = Header(default="")):
    response.headers["Cache-Control"] = "no-store"
    if not x_alert_status_token or len(x_alert_status_token) > 256:
        raise HTTPException(403, "Consulta no autorizada")
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT token_hash FROM WhatsAppAlertAccess WHERE alert_id=%s", (alert_id,))
            access = cursor.fetchone()
            actual = hashlib.sha256(x_alert_status_token.encode()).hexdigest()
            if not access or not hmac.compare_digest(actual, access["token_hash"]):
                raise HTTPException(403, "Consulta no autorizada")
            cursor.execute("""
                SELECT n.contact_id, n.name, n.message_id,
                       COALESCE(d.status, n.initial_status) AS status,
                       CASE WHEN d.message_id IS NOT NULL THEN d.error_code
                            ELSE n.error_code END AS error_code,
                       d.event_timestamp AS status_timestamp
                FROM WhatsAppNotifications n
                LEFT JOIN WhatsAppDelivery d ON d.message_id=n.message_id
                WHERE n.alert_id=%s ORDER BY n.contact_id
            """, (alert_id,))
            results = [dict(row) for row in cursor.fetchall()]
        return {"alert_id": alert_id,
                "whatsapp": {"results": results, "summary": summarize_results(results)}}
    finally:
        conn.close()
