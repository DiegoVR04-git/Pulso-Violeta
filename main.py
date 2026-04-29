import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from passlib.context import CryptContext
import psycopg2
from psycopg2.extras import RealDictCursor
from twilio.rest import Client
from fastapi.responses import HTMLResponse


# --- CONFIGURACIÓN DE TWILIO ---
# 1. Python busca las llaves secretas en la bóveda de Render
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

# 2. Inicializamos el cliente globalmente si las llaves existen
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    twilio_client = None
    print("⚠️ Advertencia: Credenciales de Twilio no encontradas.")
# -------------------------------

app = FastAPI(title="Safety App API")

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# 1. Setup Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str):
    # BLINDAJE: Cortamos la contraseña a un máximo de 50 caracteres
    # para que bcrypt nunca vuelva a crashear con el límite de 72 bytes.
    contrasena_segura = password[:50]
    return pwd_context.hash(contrasena_segura)

# Función para verificar la contraseña
def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

# 2. Define the expected data from the mobile app
class UserRegister(BaseModel):
    phone_number: str
    full_name: str
    password: str


# Define the expected data for adding a contact
class ContactCreate(BaseModel):
    user_id: int
    name: str
    phone_number: str


# Alert model
class AlertCreate(BaseModel):
    user_id: int
    latitude: float
    longitude: float

class UserLogin(BaseModel):
    phone_number: str
    password: str


# Molde para validar los datos cuando editamos un contacto
class ContactUpdate(BaseModel):
    name: str
    phone_number: str

# NUEVO MODELO PARA EL RASTREO
class TrackPoint(BaseModel):
    alert_id: int
    latitude: float
    longitude: float

# 3. Database Connection Helper
def get_db_connection():
    try:
        # 1. Python busca la URL secreta en las variables del servidor (Render)
        db_url = os.environ.get("DATABASE_URL")

        if db_url:
            # Si está en la nube, se conecta a Neon
            conn = psycopg2.connect(db_url)
        else:
            # Si db_url está vacío, asume que estás en tu computadora y usa la local
            conn = psycopg2.connect(
                host="127.0.0.1",
                database="safety_app_db",
                user="postgres",
                password="PON_TU_PASSWORD_LOCAL_AQUI"
            )
        return conn
    except Exception as e:
        print(f"Error de conexión a la BD: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    
# 4. The Registration Endpoint
@app.post("/register", status_code=201)
async def register_user(user: UserRegister):
    # ¡EL DETECTIVE! Esto imprimirá en tu terminal qué está llegando
    print(f"\n--- NUEVO INTENTO DE REGISTRO ---")
    print(f"Nombre: {user.full_name}")
    print(f"Teléfono: {user.phone_number}")
    print(f"Longitud de la contraseña recibida: {len(user.password)} caracteres!")
    print(f"---------------------------------\n")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Hash the password before saving!
        hashed_password = get_password_hash(user.password)
        
        # Insert into the database
        insert_query = """
            INSERT INTO Users (phone_number, full_name, password_hash)
            VALUES (%s, %s, %s) RETURNING user_id, phone_number, full_name;
        """
        cursor.execute(insert_query, (user.phone_number, user.full_name, hashed_password))
        new_user = cursor.fetchone()
        conn.commit()
        
        return {"message": "User created successfully", "user": new_user}

    except psycopg2.errors.UniqueViolation:
        conn.rollback() # Cancel transaction if phone exists
        raise HTTPException(status_code=400, detail="Phone number already registered")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()




# Endpoint para agregar un contacto a la lista de contactos del usuario (POST /contacts)
@app.post("/contacts", status_code=201)
async def create_contact(contact: ContactCreate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # --- ADUANA 1: Bloquear contactos duplicados ---
        # Verificamos si este usuario ya tiene registrado ese número exacto
        cursor.execute("SELECT contact_id FROM Contacts WHERE user_id = %s AND phone_number = %s;", (contact.user_id, contact.phone_number))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Este número de teléfono ya está en tu red de emergencia.")

        # --- ADUANA 2: Bloquear si ya tiene 4 contactos ---
        # Contamos cuántos contactos tiene registrados actualmente
        cursor.execute("SELECT COUNT(*) as total FROM Contacts WHERE user_id = %s;", (contact.user_id,))
        resultado = cursor.fetchone()
        
        if resultado['total'] >= 4:
            raise HTTPException(status_code=400, detail="Límite alcanzado: Tienes el máximo de 4 contactos permitidos.")

        # --- SI PASA LAS DOS ADUANAS, GUARDAMOS ---
        insert_query = """
            INSERT INTO Contacts (user_id, name, phone_number)
            VALUES (%s, %s, %s) RETURNING contact_id, name, phone_number;
        """
        cursor.execute(insert_query, (contact.user_id, contact.name, contact.phone_number))
        new_contact = cursor.fetchone()
        conn.commit()
        
        return {"message": "Contacto guardado con éxito", "contact": new_contact}

    except HTTPException:
        conn.rollback()
        raise # Deja pasar el error 400 limpio hacia el celular
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


#ENDPOINT para obtener la lista de contactos de un usuario (GET /contacts/{user_id})
@app.get("/contacts/{user_id}")
async def get_contacts(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Buscamos solo los contactos que le pertenecen a este usuario
        select_query = "SELECT contact_id, name, phone_number FROM Contacts WHERE user_id = %s;"
        cursor.execute(select_query, (user_id,))
        contacts = cursor.fetchall()
        
        return {"contacts": contacts}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()



## EMERGENCY ENDPOINT (POST /alerts)
@app.post("/alerts", status_code=201)
async def create_alert(alert: AlertCreate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. GUARDAMOS LA ALERTA EN LA BASE DE DATOS
        insert_query = """
            INSERT INTO Alerts (user_id, latitude, longitude)
            VALUES (%s, %s, %s) RETURNING alert_id, status, created_at, latitude, longitude;
        """
        cursor.execute(insert_query, (alert.user_id, alert.latitude, alert.longitude))
        new_alert = cursor.fetchone()

        # --- 🚀 PASO CLAVE: OBTENER EL NOMBRE REAL DEL USUARIO ---
        # Buscamos en la tabla Users usando el ID que mandó el celular
        cursor.execute("SELECT full_name FROM Users WHERE user_id = %s;", (alert.user_id,))
        user_info = cursor.fetchone()
        
        # Si por alguna razón no lo encuentra, usamos un respaldo
        nombre_persona = user_info['full_name'] if user_info else "Un usuario de SafetyApp"
        # -------------------------------------------------------

        # 2. BUSCAMOS A LOS PROTECTORES (Contactos)
        cursor.execute("SELECT name, phone_number FROM Contacts WHERE user_id = %s;", (alert.user_id,))
        contactos = cursor.fetchall()
        conn.commit()

        # 3. CONSTRUIMOS EL MENSAJE PERSONALIZADO
        # Extraemos el ID de la alerta que acabamos de guardar en la base de datos
        id_de_alerta = new_alert['alert_id']
        
        # Construimos el link hacia tu nuevo mapa interactivo
        map_link_en_vivo = f"https://safety-app-api.onrender.com/map/{id_de_alerta}"
        
        # Ahora el mensaje lleva el nombre real y el link al Centro de Mando
        mensaje_emergencia = f"🚨 URGENTE: {nombre_persona} ha activado su botón de pánico. Sigue su ubicación y recorrido en vivo aquí: {map_link_en_vivo}"

        print(f"\n🚨 --- TRANSMITIENDO ALERTA DE {nombre_persona.upper()} --- 🚨")
        
        try:
            if twilio_client:
                for contacto in contactos:
                    numero_destino = contacto['phone_number'] 
                    
                    message = twilio_client.messages.create(
                        body=mensaje_emergencia, 
                        from_=TWILIO_PHONE_NUMBER, 
                        to=numero_destino
                    )
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



# ENDPOINT DE LOGIN (POST /login)
@app.post("/login")
async def login_user(user: UserLogin):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Buscamos al usuario por su número de teléfono
        cursor.execute("SELECT * FROM Users WHERE phone_number = %s;", (user.phone_number,))
        db_user = cursor.fetchone()

        # 2. Si el teléfono no existe, o si la contraseña no coincide... ¡Acceso Denegado!
        if not db_user or not verify_password(user.password, db_user['password_hash']):
            raise HTTPException(
                status_code=401, 
                detail="Teléfono o contraseña incorrectos"
            )

        # 3. Si todo está bien, le damos la bienvenida y le entregamos su user_id
        return {
            "message": "Inicio de sesión exitoso", 
            "user_id": db_user['user_id'],
            "full_name": db_user['full_name']
        }

    except HTTPException:
        raise # Dejamos pasar el error 401 limpio
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ENDPOINT PARA EDITAR UN CONTACTO (PUT /contacts/{contact_id})
@app.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, contact: ContactUpdate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Ejecutamos el UPDATE real y pedimos que nos devuelva la fila editada (RETURNING)
        update_query = """
            UPDATE Contacts 
            SET name = %s, phone_number = %s 
            WHERE contact_id = %s
            RETURNING contact_id, user_id, name, phone_number;
        """
        cursor.execute(update_query, (contact.name, contact.phone_number, contact_id))
        updated_contact = cursor.fetchone()

        # 2. Si el fetchone() está vacío, significa que el contact_id no existe
        if not updated_contact:
            raise HTTPException(status_code=404, detail="Contacto no encontrado")

        # 3. Guardamos los cambios permanentemente
        conn.commit()

        # 4. Devolvemos el acuse de recibo con los datos reales de la base de datos
        return {
            "message": "Contacto actualizado exitosamente",
            "contact": updated_contact
        }

    except HTTPException:
        # Dejamos pasar el error 404 (Not Found) limpio
        conn.rollback()
        raise
    except Exception as e:
        # Si hay un error SQL, cancelamos la transacción por seguridad
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Siempre cerramos la puerta de la base de datos
        cursor.close()
        conn.close()



# ENDPOINT PARA ELIMINAR UN CONTACTO (DELETE /contacts/{contact_id})
@app.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Ejecutamos el DELETE y pedimos que devuelva el ID para confirmar que lo borró
        delete_query = "DELETE FROM Contacts WHERE contact_id = %s RETURNING contact_id;"
        cursor.execute(delete_query, (contact_id,))
        deleted_contact = cursor.fetchone()

        # 2. Si no devuelve nada, el contacto ya no existía
        if not deleted_contact:
            raise HTTPException(status_code=404, detail="Contacto no encontrado")

        # 3. Guardamos los cambios permanentemente
        conn.commit()

        # 4. Acuse de recibo
        return {"message": "Contacto eliminado exitosamente"}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ENDPOINT PARA RECIBIR COORDENADAS CONTINUAS
@app.post("/alerts/track", status_code=201)
async def add_track_point(point: TrackPoint):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        insert_query = """
            INSERT INTO TrackingData (alert_id, latitude, longitude)
            VALUES (%s, %s, %s) RETURNING tracking_id;
        """
        cursor.execute(insert_query, (point.alert_id, point.latitude, point.longitude))
        conn.commit()
        return {"message": "Punto de rastreo guardado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ENDPOINT PARA MOSTRAR EL MAPA DE RASTREO EN TIEMPO REAL
@app.get("/map/{alert_id}", response_class=HTMLResponse)
async def get_emergency_map(alert_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Obtenemos el recorrido histórico de esta alerta
        query = """
            SELECT latitude, longitude, created_at 
            FROM TrackingData 
            WHERE alert_id = %s 
            ORDER BY created_at ASC;
        """
        cursor.execute(query, (alert_id,))
        points = cursor.fetchall()

        if not points:
            return "<h1>No hay datos de rastreo para esta alerta aún.</h1>"

        # 2. Convertimos los puntos a un formato que JavaScript entienda (Lista de listas)
        # Ejemplo: [[lat1, lon1], [lat2, lon2]...]
        path_data = [[p['latitude'], p['longitude']] for p in points]
        last_point = path_data[-1]

        # 3. Construimos el HTML con Leaflet.js inyectado
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Centro de Mando - Emergencia #{alert_id}</title>
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
            <style>
                #map {{ height: 100vh; width: 100%; }}
                body {{ margin: 0; padding: 0; font-family: sans-serif; }}
                .info-box {{ position: absolute; top: 10px; left: 50px; z-index: 1000; background: white; padding: 10px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.3); }}
            </style>
        </head>
        <body>
            <div class="info-box">
                <b>🚨 Emergencia en curso</b><br>
                ID de Alerta: {alert_id}<br>
                Puntos registrados: {len(path_data)}
            </div>
            <div id="map"></div>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <script>
                var path = {path_data};
                var lastPoint = {last_point};
                
                // Inicializamos el mapa en la última ubicación
                var map = L.map('map').setView(lastPoint, 15);
                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);

                // Dibujamos la línea del recorrido
                L.polyline(path, {{color: 'red', weight: 5, opacity: 0.7}}).addTo(map);
                
                // Ponemos un marcador en la posición actual
                L.marker(lastPoint).addTo(map)
                    .bindPopup("<b>Última ubicación vista</b>").openPopup();
            </script>
        </body>
        </html>
        """
        return html_content
    except Exception as e:
        return f"<h1>Error al cargar el mapa: {str(e)}</h1>"
    finally:
        cursor.close()
        conn.close()