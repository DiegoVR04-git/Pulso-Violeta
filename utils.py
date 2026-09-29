import os
import csv
import io
import base64
import traceback
import json
from html import escape
import urllib.request
from urllib.error import HTTPError, URLError
from datetime import datetime, timedelta
from passlib.context import CryptContext
from psycopg2.extras import RealDictCursor
from database import get_db_connection

# 1. Setup Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str):
    # BLINDAJE: Cortamos la contraseña a un máximo de 50 caracteres
    contrasena_segura = password[:50]
    return pwd_context.hash(contrasena_segura)

def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

# 2. SISTEMA DE LIMPIEZA AUTOMÁTICA
def limpiar_coordenadas_antiguas():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        fecha_limite = datetime.now() - timedelta(days=7)
        
        delete_query = """
            DELETE FROM TrackingData 
            WHERE created_at < %s;
        """
        cursor.execute(delete_query, (fecha_limite,))
        
        registros_eliminados = cursor.rowcount
        conn.commit()
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n✅ [{timestamp}] LIMPIEZA DE BASE DE DATOS COMPLETADA")
        print(f"   Registros eliminados: {registros_eliminados}")
        print(f"   Fecha límite: {fecha_limite.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Próxima ejecución: 3:00 AM (todos los días)\n")
        
    except Exception as e:
        print(f"\n❌ Error en la limpieza de coordenadas: {str(e)}\n")
    finally:
        try:
            cursor.close()
            conn.close()
        except:
            pass

def _whatsapp_report_html(snapshot):
    """Renderiza únicamente la copia capturada al cerrar; no consulta estados nuevos."""
    heading = '<h3>Estado de los mensajes de WhatsApp al cerrar la alerta</h3>'
    if snapshot is None:
        return heading + '<p>No se pudieron consultar los estados al cierre.</p>'
    captured = escape(str(snapshot['captured_at']))
    note = (f'<p>Consulta durante el cierre (UTC): {captured}</p>'
            '<p>Últimos estados conocidos por el servidor en ese momento. '
            'Las confirmaciones posteriores no se reflejan en este reporte. '
            'Aceptado no significa entregado; la ausencia de confirmación de lectura '
            'no demuestra que el mensaje no se haya leído.</p>')
    results = snapshot['results']
    if not results:
        return heading + note + '<p>No hay registros de seguimiento de WhatsApp para esta alerta.</p>'
    labels = {'accepted': 'Aceptado por WhatsApp', 'pending': 'Pendiente',
              'sent': 'Enviado', 'delivered': 'Entregado', 'read': 'Leído',
              'failed': 'Falló el envío', 'unknown': 'Sin confirmación'}
    rows = []
    for item in results:
        name = escape(str(item.get('name') or 'Contacto'))
        state = labels.get(item.get('status'), 'Sin confirmación')
        rows.append(f'<tr><td style="padding:8px;border:1px solid #ddd;">{name}</td>'
                    f'<td style="padding:8px;border:1px solid #ddd;">{state}</td></tr>')
    return (heading + note + '<table style="border-collapse:collapse;">'
            '<thead><tr><th scope="col">Contacto</th><th scope="col">Estado</th></tr></thead>'
            '<tbody>' + ''.join(rows) + '</tbody></table>')


# 3. FUNCIÓN PARA ENVIAR REPORTE DE EVIDENCIA CON RESEND Y DOMINIO PROPIO
def enviar_reporte_evidencia(alert_id: int, correo_destino: str, whatsapp_snapshot=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
            SELECT latitude, longitude, created_at 
            FROM TrackingData 
            WHERE alert_id = %s 
            ORDER BY created_at ASC;
        """
        cursor.execute(query, (alert_id,))
        tracking_points = cursor.fetchall()
        cursor.close()
        conn.close()
        
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        csv_writer.writerow(['Fecha y Hora', 'Latitud', 'Longitud'])
        
        for point in tracking_points:
            csv_writer.writerow([
                point['created_at'].strftime("%Y-%m-%d %H:%M:%S"),
                point['latitude'],
                point['longitude']
            ])
        
        csv_content = csv_buffer.getvalue()
        csv_buffer.close()
        
        csv_bytes = csv_content.encode('utf-8')
        csv_base64 = base64.b64encode(csv_bytes).decode('utf-8')
        
        # --- CONFIGURACIÓN DE LA API DE RESEND ---
        api_key = os.environ.get("RESEND_API_KEY")
        
        # ¡AQUÍ ESTÁ TU DOMINIO OFICIAL VERIFICADO!
        remitente = "alertas@northsidekits.ca" 
        
        correo_limpio = str(correo_destino).strip() if correo_destino else ""
        
        if not correo_limpio or "@" not in correo_limpio:
            print(f"❌ Error: Correo inválido '{correo_limpio}' - no se puede enviar el reporte")
            return False
        
        if not api_key:
            print("❌ Error: Falta la variable de entorno RESEND_API_KEY")
            return False
        
        # Construimos el payload exacto que pide Resend
        payload = {
            "from": remitente,
            "to": [correo_limpio],
            "subject": f"Reporte de Evidencia - Safety App [CONFIDENCIAL] - Alerta #{alert_id}",
            "html": f"""
            <html>
                <body style="font-family: Arial, sans-serif; color: #333;">
                    <h2 style="color: #d32f2f;">🚨 Reporte de Evidencia de Alerta</h2>
                    <p><strong>ID de Alerta:</strong> {alert_id}</p>
                    <p><strong>Total de puntos GPS registrados:</strong> {len(tracking_points)}</p>
                    <p>Este archivo contiene un registro histórico de todas las coordenadas GPS 
                    capturadas durante esta alerta de emergencia.</p>
                    {_whatsapp_report_html(whatsapp_snapshot)}
                    <hr>
                    <p style="font-size: 12px; color: #666;">
                        <em>Este es un documento confidencial destinado únicamente al destinatario. 
                        Safety App no se responsabiliza por el uso inadecuado de esta información.</em>
                    </p>
                </body>
            </html>
            """,
            "attachments": [
                {
                    "filename": f"evidencia_{alert_id}.csv",
                    "content": csv_base64
                }
            ]
        }
        
        # Configuramos la petición HTTP pura para la API de Resend
        req = urllib.request.Request("https://api.resend.com/emails")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "SafetyAppBackend/1.0")
        
        try:
            # Disparamos la petición a la nube de Resend
            with urllib.request.urlopen(req, data=json.dumps(payload).encode('utf-8'), timeout=10) as response:
                response_code = response.getcode()
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n✅ [{timestamp}] REPORTE DE EVIDENCIA ENVIADO VÍA RESEND")
            print(f"   Alerta ID: {alert_id}\n   Destinatario: {correo_limpio}\n   Status Code: {response_code}\n")
            
            return 200 <= response_code < 300

        except HTTPError as e:
            error_info = e.read().decode('utf-8')
            print(f"\n❌ Error devuelto por Resend (HTTP {e.code}): {error_info}\n")
        except URLError as e:
            print(f"\n❌ Error de red al contactar a Resend: {e.reason}\n")

    except Exception as e:
        print(f"\n❌ Error general al generar/enviar reporte de evidencia: {str(e)}\n")
        print(traceback.format_exc())
        print("\n")

    return False
