import os
import random
import resend
from fastapi import APIRouter, HTTPException
from psycopg2.extras import RealDictCursor
import psycopg2
from database import get_db_connection

# IMPORTANTE: Asegúrate de tener estos nuevos esquemas en tu schemas.py
from schemas import (
    UserRegister, UserLogin, UserProfileUpdate, 
    SendCodeRequest, VerifyCodeRequest, ResetPasswordRequest
)
from utils import get_password_hash, verify_password

# Configura tu API Key de Resend (Asegúrate de agregarla a tus variables de entorno en Render)
resend.api_key = os.environ.get("RESEND_API_KEY", "TU_API_KEY_AQUI")

# Instanciamos el router
router = APIRouter(tags=["Auth"])


@router.post("/register", status_code=201)
async def register_user(user: UserRegister):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        hashed_password = get_password_hash(user.password)
        # SOLUCIÓN 1: Agregamos 'email' al INSERT
        insert_query = """
            INSERT INTO Users (phone_number, full_name, email, password_hash)
            VALUES (%s, %s, %s, %s) RETURNING user_id, phone_number, full_name, email;
        """
        cursor.execute(insert_query, (user.phone_number, user.full_name, user.email, hashed_password))
        new_user = cursor.fetchone()
        conn.commit()
        return {"message": "User created successfully", "user": new_user}

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Phone number already registered")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/login")
async def login_user(user: UserLogin):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM Users WHERE phone_number = %s;", (user.phone_number,))
        db_user = cursor.fetchone()

        if not db_user or not verify_password(user.password, db_user['password_hash']):
            raise HTTPException(status_code=401, detail="Teléfono o contraseña incorrectos")

        # ¡Adiós al truco maestro! Ahora tomamos los correos directamente de la tabla Users
        return {
            "message": "Inicio de sesión exitoso", 
            "user_id": db_user['user_id'],
            "full_name": db_user['full_name'],
            "email": db_user['email'],         # Correo personal
            "sos_email": db_user['sos_email']  # Correo de emergencias (puede ser null al inicio)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# RUTA PARA ACTUALIZAR EL PERFIL DEL USUARIO
@router.put("/users/{user_id}/profile")
async def update_profile(user_id: int, profile: UserProfileUpdate):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # SOLUCIÓN 3: Agregamos 'sos_email' al UPDATE
        update_query = """
            UPDATE Users 
            SET full_name = %s, phone_number = %s, email = %s, sos_email = %s 
            WHERE user_id = %s;
        """
        cursor.execute(update_query, (profile.full_name, profile.phone_number, profile.email, profile.sos_email, user_id))
        conn.commit()
        return {"message": "Perfil actualizado con éxito"}

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Este número de teléfono ya está registrado en otra cuenta.")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ==========================================
# RUTAS DE RECUPERACIÓN DE CONTRASEÑA (NUEVO)
# ==========================================

@router.post("/auth/send-code")
async def send_verification_code(request: SendCodeRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Verificar que el correo exista en nuestra base de datos
        cursor.execute("SELECT user_id FROM Users WHERE email = %s;", (request.email,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="No existe una cuenta con este correo.")

        # 2. Generar un código aleatorio de 6 dígitos
        codigo = str(random.randint(100000, 999999))

        # 3. Guardar o actualizar el código en la base de datos
        cursor.execute("DELETE FROM verification_codes WHERE email = %s;", (request.email,))
        cursor.execute(
            "INSERT INTO verification_codes (email, code) VALUES (%s, %s);", 
            (request.email, codigo)
        )
        conn.commit()

        # 4. Enviar el correo con Resend y captura detallada de error
        try:
            print("Intentando enviar correo con Resend...")
            resend.Emails.send({
                "from": "onboarding@resend.dev",
                "to": [request.email],
                "subject": "Tu código de seguridad - Pulso Violeta",
                "html": f"""
                <div style="font-family: sans-serif; text-align: center; padding: 20px;">
                    <h2 style="color: #5F42CA;">Pulso Violeta</h2>
                    <p>Usa el siguiente código de 6 dígitos para recuperar tu acceso:</p>
                    <h1 style="background-color: #F4EEFF; padding: 15px; letter-spacing: 5px; color: #37246B; border-radius: 10px;">
                        {codigo}
                    </h1>
                    <p style="color: #666; font-size: 12px;">Si no solicitaste este código, ignora este correo.</p>
                </div>
                """
            })
            print("¡Correo enviado con éxito por Resend!")
        except Exception as resend_ex:
            print(f"❌ ERROR CRÍTICO DE RESEND: {str(resend_ex)}")
            raise HTTPException(status_code=500, detail=f"Error al enviar correo: {str(resend_ex)}")

        return {"message": "Código enviado con éxito"}

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/auth/verify-code")
async def verify_code(request: VerifyCodeRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Buscamos el último código generado para este correo
        cursor.execute(
            "SELECT code FROM verification_codes WHERE email = %s ORDER BY created_at DESC LIMIT 1;", 
            (request.email,)
        )
        record = cursor.fetchone()

        if not record or record['code'] != request.code:
            raise HTTPException(status_code=400, detail="Código inválido o expirado.")

        return {"message": "Código verificado correctamente"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.put("/auth/reset-password")
async def reset_password(request: ResetPasswordRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Volvemos a verificar el código por seguridad antes de cambiar la contraseña
        cursor.execute(
            "SELECT code FROM verification_codes WHERE email = %s ORDER BY created_at DESC LIMIT 1;", 
            (request.email,)
        )
        record = cursor.fetchone()

        if not record or record['code'] != request.code:
            raise HTTPException(status_code=400, detail="Código inválido. Intenta de nuevo.")

        # 2. Hasheamos la nueva contraseña
        new_hashed_password = get_password_hash(request.new_password)

        # 3. Actualizamos la contraseña en la tabla Users
        cursor.execute(
            "UPDATE Users SET password_hash = %s WHERE email = %s;", 
            (new_hashed_password, request.email)
        )
        
        # 4. Borramos el código usado para que no se pueda reutilizar
        cursor.execute("DELETE FROM verification_codes WHERE email = %s;", (request.email,))
        
        conn.commit()

        return {"message": "Contraseña actualizada con éxito"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()