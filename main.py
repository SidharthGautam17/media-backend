from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os, sys, json, importlib, pkgutil
from datetime import datetime
from shutil import copyfile
from openai import OpenAI
from backend.app import main

app = main.app

# Optional: Add a root test endpoint
@app.get("/")
def read_root():
    return {"status": "ok", "message": "Backend is running!"}

# =========================
# File paths
# =========================
USERS_FILE = "backend/app/users.json"
SETTINGS_FILE = "backend/app/settings.json"
PATCH_DIR = "backend/app/patches"
BACKUP_DIR = "backend/app/backups"

# Ensure runtime directories exist
os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
os.makedirs(PATCH_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# =========================
# FastAPI app
# =========================
app = FastAPI()

# =========================
# Helpers
# =========================
def load_users():
    if not os.path.exists(USERS_FILE):
        save_users({})
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        save_settings({})
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

def ensure_admin_user():
    users = load_users()
    if "admin" not in users:
        users["admin"] = {"password": "admin", "role": "admin"}
        save_users(users)
        print("✅ Default admin user created: admin / admin")
    return users

ensure_admin_user()

# =========================
# Models
# =========================
class User(BaseModel):
    email: str
    password: str

class Prompt(BaseModel):
    prompt: str

# =========================
# Auth APIs
# =========================
@app.post("/api/signup")
def signup(user: User):
    users = load_users()
    if user.email in users:
        raise HTTPException(status_code=400, detail="User already exists")
    users[user.email] = {"password": user.password, "role": "user"}
    save_users(users)
    return {"message": "Signup successful"}

@app.post("/api/login")
def login(user: User):
    users = load_users()
    if user.email not in users or users[user.email]["password"] != user.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"email": user.email, "role": users[user.email].get("role", "user")}

# =========================
# Settings API (with API key & model support)
# =========================
@app.get("/api/settings")
def get_settings():
    settings = load_settings()
    # Mask API key in UI
    if "openai_api_key" in settings:
        settings["openai_api_key"] = "*****"
    return settings

@app.post("/api/settings")
def update_settings(data: dict):
    settings = load_settings()

    # Update sensitive fields
    if "openai_api_key" in data and data["openai_api_key"] not in ("", "*****"):
        settings["openai_api_key"] = data["openai_api_key"]

    if "model" in data and data["model"]:
        settings["model"] = data["model"]

    # Update other settings
    for k, v in data.items():
        if k not in ("openai_api_key", "model"):
            settings[k] = v

    save_settings(settings)
    return {"message": "Settings updated"}

# =========================
# AI Agent Endpoint
# =========================
@app.post("/api/agent")
def agent(prompt: Prompt):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key and os.path.exists(SETTINGS_FILE):
        try:
            settings = load_settings()
            api_key = settings.get("openai_api_key")
        except Exception:
            api_key = None

    if not api_key:
        raise HTTPException(status_code=500, detail="❌ No OpenAI API key found. Add it in settings.json or via Admin UI.")

    client = OpenAI(api_key=api_key)

    try:
        settings = load_settings()
        model = settings.get("model", "gpt-4.1-mini")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are an AI system that helps improve this app. Always return JSON."},
                {"role": "user", "content": prompt.prompt}
            ]
        )

        reply = response.choices[0].message.content

        try:
            parsed = json.loads(reply)
        except:
            parsed = {"message": reply}

        if "file" in parsed and "code" in parsed:
            filename = parsed["file"].replace("/", "_")
            patch_file = os.path.join(PATCH_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}")
            with open(patch_file, "w", encoding="utf-8") as f:
                f.write(parsed["code"])
            parsed["patch_saved"] = patch_file

        return parsed

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# Patches & Rollback APIs
# =========================
@app.get("/api/patches")
def list_patches():
    return sorted(os.listdir(PATCH_DIR), reverse=True)

@app.get("/api/patches/{name}")
def get_patch(name: str):
    path = os.path.join(PATCH_DIR, name)
    if os.path.exists(path):
        return FileResponse(path, media_type="text/plain")
    raise HTTPException(status_code=404, detail="Patch not found")

@app.post("/api/patches/apply/{name}")
def apply_patch(name: str):
    patch_path = os.path.join(PATCH_DIR, name)
    if not os.path.exists(patch_path):
        raise HTTPException(status_code=404, detail="Patch not found")

    try:
        target_file = name.split("_", 2)[-1].replace("_", "/")
        target_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../", target_file))

        if os.path.exists(target_path):
            backup_name = os.path.basename(target_file) + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            copyfile(target_path, backup_path)
        else:
            backup_path = None

        copyfile(patch_path, target_path)

        return {"message": f"✅ Patch {name} applied to {target_file}", "backup": backup_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to apply patch: {str(e)}")

@app.get("/api/backups")
def list_backups():
    return sorted(os.listdir(BACKUP_DIR), reverse=True)

@app.post("/api/backups/rollback/{name}")
def rollback_backup(name: str):
    backup_path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Backup not found")

    try:
        target_file = "_".join(name.split(".bak_")[0].split("_"))
        target_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../", target_file))
        copyfile(backup_path, target_path)
        return {"message": f"✅ Rolled back {target_file} from backup {name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rollback: {str(e)}")

# =========================
# Frontend mount (PyInstaller safe)
# =========================
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))

frontend_path = os.path.join(base_path, "frontend/dist")

if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

@app.get("/")
def frontend():
    index_file = os.path.join(frontend_path, "index.html")
    if not os.path.exists(index_file):
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_file)

@app.get("/api/health")
def health():
    return {"msg": "Media Backend Running"}

# =========================
# Auto-load backend plugins
# =========================
import backend.app.plugins
for _, module_name, _ in pkgutil.iter_modules(backend.app.plugins.__path__):
    module = importlib.import_module(f"backend.app.plugins.{module_name}")
    if hasattr(module, "router"):
        app.include_router(module.router)
        print(f"✅ Loaded plugin: {module_name}")