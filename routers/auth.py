from fastapi import APIRouter, HTTPException
from psycopg2.extras import RealDictCursor
import psycopg2
from database import get_db_connection
from schemas import UserRegister, UserLogin, UserProfileUpdate
from utils import get_password_hash, verify_password

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

        # 👇 EL TRUCO MAESTRO: Buscar el correo en tu historial de alertas 👇
        cursor.execute("""
            SELECT email FROM Alerts 
            WHERE user_id = %s AND email IS NOT NULL AND email != 'anonimo' 
            ORDER BY created_at DESC LIMIT 1;
        """, (db_user['user_id'],))
        historial = cursor.fetchone()
        
        # Si encuentra un correo anterior tuyo, lo usa. Si no, manda anonimo.
        correo_recuperado = historial['email'] if historial else 'anonimo'

        return {
            "message": "Inicio de sesión exitoso", 
            "user_id": db_user['user_id'],
            "full_name": db_user['full_name'],
            "email": correo_recuperado 
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