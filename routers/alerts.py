import os
from typing import List
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from psycopg2.extras import RealDictCursor
from twilio.rest import Client
from database import get_db_connection
from schemas import AlertCreate, TrackPoint
from utils import enviar_reporte_evidencia

router = APIRouter(tags=["Alerts"])

# --- CONFIGURACIÓN DE TWILIO ---
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    twilio_client = None
    print("⚠️ Advertencia: Credenciales de Twilio no encontradas.")

@router.post("/alerts", status_code=201)
async def create_alert(alert: AlertCreate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        insert_query = """
            INSERT INTO Alerts (user_id, latitude, longitude, email)
            VALUES (%s, %s, %s, %s) RETURNING alert_id, status, created_at, latitude, longitude, email;
        """
        cursor.execute(insert_query, (alert.user_id, alert.latitude, alert.longitude, alert.email))
        new_alert = cursor.fetchone()

        cursor.execute("SELECT full_name FROM Users WHERE user_id = %s;", (alert.user_id,))
        user_info = cursor.fetchone()
        nombre_persona = user_info['full_name'] if user_info else "Un usuario de SafetyApp"

        cursor.execute("SELECT name, phone_number FROM Contacts WHERE user_id = %s;", (alert.user_id,))
        contactos = cursor.fetchall()
        conn.commit()

        id_de_alerta = new_alert['alert_id']
        map_link_en_vivo = f"https://safety-app-api.onrender.com/map/{id_de_alerta}"
        mensaje_emergencia = f"🚨 URGENTE: {nombre_persona} ha activado su botón de pánico. Sigue su ubicación en vivo: {map_link_en_vivo}"

        print(f"\n🚨 --- TRANSMITIENDO ALERTA DE {nombre_persona.upper()} --- 🚨")
        
        try:
            if twilio_client:
                for contacto in contactos:
                    numero_destino = contacto['phone_number'] 
                    twilio_client.messages.create(body=mensaje_emergencia, from_=TWILIO_PHONE_NUMBER, to=numero_destino)
                    print(f"✅ SMS enviado a {contacto['name']} ({numero_destino})")
            else:
                print("❌ Twilio no configurado.")
        except Exception as twilio_error:
            print(f"❌ Error de Twilio: {twilio_error}")

        return {"message": "Alerta registrada y red notificada", "alert": new_alert}

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
        cursor.execute("SELECT email FROM Alerts WHERE alert_id = %s;", (alert_id,))
        alert_info = cursor.fetchone()
        if not alert_info:
            raise HTTPException(status_code=404, detail="Alerta no encontrada")
        
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
        cursor.execute("INSERT INTO TrackingData (alert_id, latitude, longitude) VALUES (%s, %s, %s) RETURNING tracking_id;", (point.alert_id, point.latitude, point.longitude))
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
        cursor.execute("SELECT latitude, longitude, created_at FROM TrackingData WHERE alert_id = %s ORDER BY created_at ASC;", (alert_id,))
        points = cursor.fetchall()

        if not points:
            return f"""
            <!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0">
            <script>setTimeout(function() {{ window.location.reload(1); }}, 3000);</script></head>
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
            <script>
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