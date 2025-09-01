from fastapi import FastAPI
from backend.app.main import app as backend_app

# Re-export the backend app so Render can find it
app = backend_app

# Optional: simple health check
@app.get("/healthz")
def healthz():
    return {"status": "ok", "message": "Backend is running on Render"}
