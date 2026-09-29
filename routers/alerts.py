import os
import asyncio
from typing import List
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from psycopg2.extras import RealDictCursor

from database import get_db_connection
from schemas import AlertCreate, TrackPoint
from utils import enviar_reporte_evidencia
from whatsapp_results import classify_response, summarize_results

router = APIRouter(tags=["Alerts"])

# --- CONFIGURACIÓN DE WHATSAPP CLOUD API (META) ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")

async def send_whatsapp_alert(destinatario: str, nombre_persona: str, id_de_alerta: int) -> dict:
    """
    Envía un mensaje de plantilla usando la API oficial de WhatsApp Cloud.
    Usa la plantilla 'sos_alerta' con botón dinámico hacia el mapa en vivo.
    """
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return {"status": "failed", "message_id": None, "error_code": "not_configured"}

    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    # WhatsApp requiere el código de país sin '+', espacios o guiones
    numero_limpio = destinatario.replace("+", "").replace(" ", "").replace("-", "")
    link_mapa = f"https://safety-app-api.onrender.com/map/{id_de_alerta}"

    payload = {
            "messaging_product": "whatsapp",
            "to": numero_limpio,
            "type": "template",
            "template": {
                "name": "sos_alerta",
                "language": {
                    "code": "es_MX" 
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": nombre_persona},
                            {"type": "text", "text": link_mapa}
                        ]
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {"type": "text", "text": str(id_de_alerta)}
                        ]
                    }
                ]
            }
        }

    # 2. RESPALDO TEMPORAL (Descomentar solo si la plantilla sigue en revisión y necesitas probar conectividad)
    # payload = {
    #     "messaging_product": "whatsapp",
    #     "to": numero_limpio,
    #     "type": "template",
    #     "template": {
    #         "name": "hello_world",
    #         "language": {"code": "en_US"}
    #     }
    # }

    async with httpx.AsyncClient() as client:
        try:
            response = await asyncio.wait_for(
                client.post(url, json=payload, headers=headers, timeout=10.0),
                timeout=12.0,
            )
            try:
                body = response.json()
            except ValueError:
                body = None
            return classify_response(response.status_code, body)
        except Exception:
            # Un timeout no demuestra que Meta haya rechazado el mensaje.
            # No reintentar automáticamente: podría duplicar la alerta.
            return {"status": "unknown", "message_id": None, "error_code": "request_unconfirmed"}


@router.post("/alerts", status_code=201)
async def create_alert(alert: AlertCreate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        correo_final = alert.sos_email if alert.sos_email and alert.sos_email != "anonimo" else None

        insert_query = """
            INSERT INTO Alerts (user_id, latitude, longitude, email)
            VALUES (%s, %s, %s, %s) RETURNING alert_id, status, created_at, latitude, longitude, email;
        """
        cursor.execute(insert_query, (alert.user_id, alert.latitude, alert.longitude, correo_final))
        new_alert = cursor.fetchone()

        cursor.execute("SELECT full_name FROM Users WHERE user_id = %s;", (alert.user_id,))
        user_info = cursor.fetchone()
        nombre_persona = user_info['full_name'] if user_info else "Un usuario de Pulso Violeta"

        cursor.execute("SELECT contact_id, name, phone_number FROM Contacts WHERE user_id = %s ORDER BY contact_id;", (alert.user_id,))
        contactos = cursor.fetchall()
        conn.commit()

        id_de_alerta = new_alert['alert_id']

        # Los cuatro contactos permitidos se procesan concurrentemente.
        outcomes = await asyncio.gather(*(
            send_whatsapp_alert(contacto['phone_number'], nombre_persona, id_de_alerta)
            for contacto in contactos
        ), return_exceptions=True)
        results = []
        for contacto, outcome in zip(contactos, outcomes):
            if isinstance(outcome, BaseException):
                outcome = {"status": "unknown", "message_id": None, "error_code": "request_unconfirmed"}
            results.append({"contact_id": contacto['contact_id'], "name": contacto['name'], **outcome})

        return {
            "message": "Alerta registrada. Consulta el resultado de WhatsApp por contacto.",
            "alert": new_alert,
            "whatsapp": {"results": results, "summary": summarize_results(results)},
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.put("/alerts/{alert_id}")
async def deactivate_alert(alert_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT email, status FROM Alerts WHERE alert_id = %s FOR UPDATE;", (alert_id,))
        alert_info = cursor.fetchone()
        if not alert_info:
            raise HTTPException(status_code=404, detail="Alerta no encontrada")

        # Reintentar un cierre ya confirmado no vuelve a enviar el reporte.
        if alert_info['status'] == 'inactive':
            conn.commit()
            return {"message": "Alerta ya desactivada",
                    "alert": {"alert_id": alert_id, "status": "inactive"},
                    "reporte_enviado": False}

        correo_destino = alert_info['email']

        update_query = "UPDATE Alerts SET status = 'inactive' WHERE alert_id = %s RETURNING alert_id, status;"
        cursor.execute(update_query, (alert_id,))
        updated_alert = cursor.fetchone()
        conn.commit()

        if correo_destino:
            try:
                enviar_reporte_evidencia(alert_id, correo_destino)
            except Exception as email_error:
                print(f"⚠️ Advertencia: No se pudo enviar el reporte: {str(email_error)}")

        return {"message": "Alerta desactivada", "alert": updated_alert, "reporte_enviado": correo_destino is not None}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/alerts/track", status_code=201)
async def add_track_point(point: TrackPoint):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "INSERT INTO TrackingData (alert_id, latitude, longitude) VALUES (%s, %s, %s) RETURNING tracking_id;",
            (point.alert_id, point.latitude, point.longitude)
        )
        conn.commit()
        return {"message": "Punto guardado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/alerts/track/batch", status_code=201)
async def add_track_points_batch(points: List[TrackPoint]):
    if not points:
        raise HTTPException(status_code=400, detail="La lista de puntos no puede estar vacía")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        data = [(p.alert_id, p.latitude, p.longitude) for p in points]
        cursor.executemany("INSERT INTO TrackingData (alert_id, latitude, longitude) VALUES (%s, %s, %s);", data)
        conn.commit()
        return {"message": f"Se guardaron {len(points)} puntos", "count": len(points)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/map/{alert_id}", response_class=HTMLResponse)
async def get_emergency_map(alert_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT latitude, longitude, created_at FROM TrackingData WHERE alert_id = %s ORDER BY created_at ASC;",
            (alert_id,)
        )
        points = cursor.fetchall()

        if not points:
            return """
            <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0">
            <script>setTimeout(function() { window.location.reload(1); }, 3000);</script></head>
            <body style="text-align:center; font-family:sans-serif; margin-top:20vh;">
                <h2>Conectando con el dispositivo...</h2><p>Estableciendo conexión GPS segura...</p>
            </body></html>
            """

        path_data = [[p['latitude'], p['longitude']] for p in points]
        last_point = path_data[-1]

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
            <style>#map {{ height: 100vh; width: 100%; }} body {{ margin: 0; padding: 0; font-family: sans-serif; }}</style>
            <script>setTimeout(function() {{ window.location.reload(1); }}, 9000);</script>
        </head>
        <body>
            <div id="map"></div>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <scrip
                var path = {path_data}; var lastPoint = {last_point};
                var map = L.map('map').setView(lastPoint, 16);
                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);
                L.polyline(path, {{color: '#FF5252', weight: 5, opacity: 0.8}}).addTo(map);
                L.marker(lastPoint).addTo(map).bindPopup("<b>Ubicación actual</b>").openPopup();
            </script>
        </body>
        </html>
        """
    except Exception as e:
        return f"<h1>Error al cargar el mapa: {str(e)}</h1>"
    finally:
        cursor.close()
        conn.close()
