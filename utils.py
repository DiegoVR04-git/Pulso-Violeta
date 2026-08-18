import os
import csv
import io
import base64
import traceback
from datetime import datetime, timedelta
from passlib.context import CryptContext
import sendgrid
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

# 3. FUNCIÓN PARA ENVIAR REPORTE DE EVIDENCIA
def enviar_reporte_evidencia(alert_id: int, correo_destino: str):
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
        
        api_key = os.environ.get("SENDGRID_API_KEY")
        remitente = os.environ.get("CORREO_REMITENTE")
        correo_limpio = str(correo_destino).strip() if correo_destino else ""
        
        if not correo_limpio or "@" not in correo_limpio:
            print(f"❌ Error: Correo inválido '{correo_limpio}' - no se puede enviar el reporte")
            return
        
        if not api_key or not remitente:
            print("❌ Error: Faltan variables de entorno (SENDGRID_API_KEY o CORREO_REMITENTE)")
            return
        
        mensaje_json = {
            "personalizations": [{"to": [{"email": correo_limpio}], "subject": f"Reporte de Evidencia - Safety App [CONFIDENCIAL] - Alerta #{alert_id}"}],
            "from": {"email": remitente},
            "content": [{
                "type": "text/html",
                "value": f"""
                <html>
                    <body style="font-family: Arial, sans-serif; color: #333;">
                        <h2 style="color: #d32f2f;">🚨 Reporte de Evidencia de Alerta</h2>
                        <p><strong>ID de Alerta:</strong> {alert_id}</p>
                        <p><strong>Total de puntos GPS registrados:</strong> {len(tracking_points)}</p>
                        <p>Este archivo contiene un registro histórico de todas las coordenadas GPS 
                        capturadas durante esta alerta de emergencia.</p>
                        <hr>
                        <p style="font-size: 12px; color: #666;">
                            <em>Este es un documento confidencial destinado únicamente al destinatario. 
                            Safety App no se responsabiliza por el uso inadecuado de esta información.</em>
                        </p>
                    </body>
                </html>
                """
            }],
            "attachments": [{"content": csv_base64, "type": "text/csv", "filename": f"evidencia_{alert_id}.csv", "disposition": "attachment"}]
        }
        
        try:
            sg = sendgrid.SendGridAPIClient(api_key=api_key)
            respuesta = sg.client.mail.send.post(request_body=mensaje_json)
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n✅ [{timestamp}] REPORTE DE EVIDENCIA ENVIADO VÍA SENDGRID")
            print(f"   Alerta ID: {alert_id}\n   Destinatario: {correo_limpio}\n   Status Code: {respuesta.status_code}\n")
            
        except Exception as sendgrid_error:
            print(f"\n❌ Error al enviar con SendGrid: {str(sendgrid_error)}")
            print(traceback.format_exc())
            print("\n")

    except Exception as e:
        print(f"\n❌ Error al generar/enviar reporte de evidencia: {str(e)}\n")
        print(traceback.format_exc())
        print("\n")