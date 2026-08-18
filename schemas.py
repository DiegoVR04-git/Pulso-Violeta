from pydantic import BaseModel

# 1. Define the expected data from the mobile app
class UserRegister(BaseModel):
    phone_number: str
    full_name: str
    password: str

# 2. Define the expected data for adding a contact
class ContactCreate(BaseModel):
    user_id: int
    name: str
    phone_number: str

# 3. Alert model
class AlertCreate(BaseModel):
    user_id: int
    latitude: float
    longitude: float
    email: str

# 4. User Login model
class UserLogin(BaseModel):
    phone_number: str
    password: str

# 5. Molde para validar los datos cuando editamos un contacto
class ContactUpdate(BaseModel):
    name: str
    phone_number: str

# 6. NUEVO MODELO PARA EL RASTREO
class TrackPoint(BaseModel):
    alert_id: int
    latitude: float
    longitude: float