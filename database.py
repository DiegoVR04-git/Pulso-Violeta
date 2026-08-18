import os
import psycopg2
from fastapi import HTTPException

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