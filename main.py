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




from fastapi import Query, Response

VERIFY_TOKEN = "pulso_violeta_webhook_2026"

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido")

@app.post("/webhook")
async def receive_webhook(data: dict):
    # Procesa eventos o estados de entrega si es necesario
    return {"status": "received"}