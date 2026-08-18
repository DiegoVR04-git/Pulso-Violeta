from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Importamos las herramientas sueltas que necesitamos aquí
from utils import limpiar_coordenadas_antiguas

# Importamos nuestras rutas recién empaquetadas
from routers import auth, contacts, alerts

app = FastAPI(title="Safety App API")

# Conectamos las rutas a la aplicación principal
app.include_router(auth.router)
app.include_router(contacts.router)
app.include_router(alerts.router)

# Health check (lo dejamos aquí para verificar que el servidor base vive)
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Inicializar el scheduler en background
scheduler = BackgroundScheduler()

scheduler.add_job(
    limpiar_coordenadas_antiguas,
    CronTrigger(hour=3, minute=0, timezone='America/Mexico_City'),
    id="limpieza_coordenadas",
    name="Limpieza de coordenadas GPS antiguas",
    replace_existing=True
)

@app.on_event("startup")
async def startup_event():
    scheduler.start()
    print("\n🚀 Scheduler iniciado. Las tareas programadas están activas.\n")

@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()
    print("\n🛑 Scheduler detenido.\n")