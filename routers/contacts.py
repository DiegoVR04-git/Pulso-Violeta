from fastapi import APIRouter, HTTPException
from psycopg2.extras import RealDictCursor
from database import get_db_connection
from schemas import ContactCreate, ContactUpdate

router = APIRouter(tags=["Contacts"])

@router.post("/contacts", status_code=201)
async def create_contact(contact: ContactCreate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT contact_id FROM Contacts WHERE user_id = %s AND phone_number = %s;", (contact.user_id, contact.phone_number))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Este número de teléfono ya está en tu red de emergencia.")

        cursor.execute("SELECT COUNT(*) as total FROM Contacts WHERE user_id = %s;", (contact.user_id,))
        resultado = cursor.fetchone()
        
        if resultado['total'] >= 4:
            raise HTTPException(status_code=400, detail="Límite alcanzado: Tienes el máximo de 4 contactos permitidos.")

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
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@router.get("/contacts/{user_id}")
async def get_contacts(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT contact_id, name, phone_number FROM Contacts WHERE user_id = %s;", (user_id,))
        contacts = cursor.fetchall()
        return {"contacts": contacts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@router.put("/contacts/{contact_id}")
async def update_contact(contact_id: int, contact: ContactUpdate):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        update_query = """
            UPDATE Contacts SET name = %s, phone_number = %s WHERE contact_id = %s
            RETURNING contact_id, user_id, name, phone_number;
        """
        cursor.execute(update_query, (contact.name, contact.phone_number, contact_id))
        updated_contact = cursor.fetchone()

        if not updated_contact:
            raise HTTPException(status_code=404, detail="Contacto no encontrado")

        conn.commit()
        return {"message": "Contacto actualizado exitosamente", "contact": updated_contact}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("DELETE FROM Contacts WHERE contact_id = %s RETURNING contact_id;", (contact_id,))
        deleted_contact = cursor.fetchone()

        if not deleted_contact:
            raise HTTPException(status_code=404, detail="Contacto no encontrado")

        conn.commit()
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