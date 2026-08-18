from fastapi import APIRouter, HTTPException
from psycopg2.extras import RealDictCursor
import psycopg2
from database import get_db_connection
from schemas import UserRegister, UserLogin
from utils import get_password_hash, verify_password

# Instanciamos el router
router = APIRouter(tags=["Auth"])

@router.post("/register", status_code=201)
async def register_user(user: UserRegister):
    print(f"\n--- NUEVO INTENTO DE REGISTRO ---")
    print(f"Nombre: {user.full_name}")
    print(f"Teléfono: {user.phone_number}")
    print(f"Longitud de la contraseña recibida: {len(user.password)} caracteres!")
    print(f"---------------------------------\n")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        hashed_password = get_password_hash(user.password)
        insert_query = """
            INSERT INTO Users (phone_number, full_name, password_hash)
            VALUES (%s, %s, %s) RETURNING user_id, phone_number, full_name;
        """
        cursor.execute(insert_query, (user.phone_number, user.full_name, hashed_password))
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

        return {
            "message": "Inicio de sesión exitoso", 
            "user_id": db_user['user_id'],
            "full_name": db_user['full_name']
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()