import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from passlib.context import CryptContext
import psycopg2
from psycopg2.extras import RealDictCursor
from twilio.rest import Client


# --- CONFIGURACIÓN DE TWILIO ---
TWILIO_ACCOUNT_SID = "AC_TU_SID_AQUI"
TWILIO_AUTH_TOKEN = "TU_TOKEN_AQUI"
TWILIO_PHONE_NUMBER = "+1234567890" # El número que Twilio te regalará
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



#Endpoint para agregar un contacto a la lista de contactos del usuario (POST /contacts)

@app.post("/contacts", status_code=201)
async def create_contact(contact: ContactCreate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Insertamos el contacto conectándolo con el user_id
        insert_query = """
            INSERT INTO Contacts (user_id, name, phone_number)
            VALUES (%s, %s, %s) RETURNING contact_id, name, phone_number;
        """
        cursor.execute(insert_query, (contact.user_id, contact.name, contact.phone_number))
        new_contact = cursor.fetchone()
        conn.commit()
        
        return {"message": "Contacto guardado con éxito", "contact": new_contact}

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
        # 1. GUARDAMOS LA ALERTA (Lo que ya hacíamos)
        insert_query = """
            INSERT INTO Alerts (user_id, latitude, longitude)
            VALUES (%s, %s, %s) RETURNING alert_id, status, created_at, latitude, longitude;
        """
        cursor.execute(insert_query, (alert.user_id, alert.latitude, alert.longitude))
        new_alert = cursor.fetchone()

        # 2. BUSCAMOS A LA RED DE APOYO (La nueva magia)
        # Le preguntamos a la base de datos: "¿Quiénes son los contactos del usuario 1?"
        cursor.execute("SELECT name, phone_number FROM Contacts WHERE user_id = %s;", (alert.user_id,))
        contactos = cursor.fetchall()
        conn.commit()

        # 3. EL MEGÁFONO: MANDAMOS LOS SMS
        map_link = f"https://www.google.com/maps/search/?api=1&query={alert.latitude},{alert.longitude}"
        mensaje_emergencia = f"🚨 URGENTE: El usuario ha activado su botón de pánico. Ubicación GPS: {map_link}"

        print("\n🚨 --- INICIANDO TRANSMISIÓN DE EMERGENCIA --- 🚨")
        
        # OJO: Como no tienes las llaves reales de Twilio aún, 
        # envolveremos esto en un "try" para que no crashee tu app, solo simule el envío.
        try:
            twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            
            for contacto in contactos:
                # En la vida real, Twilio exige que los números lleven el código de país (ej. +52 o +1)
                numero_destino = f"+{contacto['phone_number']}" 
                
                # Descomenta la siguiente línea cuando tengas tu cuenta real de Twilio:
                # message = twilio_client.messages.create(body=mensaje_emergencia, from_=TWILIO_PHONE_NUMBER, to=numero_destino)
                
                print(f"✅ SMS Simulado enviado a {contacto['name']} ({numero_destino})")
                
        except Exception as twilio_error:
            print("⚠️ Aviso: Twilio no configurado. Simulando envío en consola.")
            for contacto in contactos:
                print(f"📱 (Simulación) SMS a {contacto['name']}: {mensaje_emergencia}")

        print("-------------------------------------------------\n")
        
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