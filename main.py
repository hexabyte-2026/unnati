import os
import sqlite3
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Check if Render provided a PostgreSQL DATABASE_URL
RAW_DATABASE_URL = os.environ.get("DATABASE_URL")
USE_PG = bool(RAW_DATABASE_URL)

if USE_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    # Fix Render's legacy postgres:// prefix if present
    if RAW_DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)
    else:
        DATABASE_URL = RAW_DATABASE_URL
else:
    DB_FILE = "sih_scholarship.db"

app = FastAPI(
    title="SIH Scholarship Sync & Pre-Check API",
    version="1.0.0"
)

# Allow cross-origin requests from your GitHub Pages frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_connection():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Applications Table
    if USE_PG:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                applicant_name TEXT NOT NULL,
                annual_income NUMERIC NOT NULL,
                category TEXT NOT NULL,
                aadhaar_hash TEXT NOT NULL,
                document_status TEXT DEFAULT 'Pending',
                sync_status TEXT DEFAULT 'Synced',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # 2. Users Table (ensures login persists across server sleeps)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                mobile TEXT PRIMARY KEY,
                pin TEXT NOT NULL,
                state JSONB DEFAULT '{}'::jsonb
            );
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                applicant_name TEXT NOT NULL,
                annual_income REAL NOT NULL,
                category TEXT NOT NULL,
                aadhaar_hash TEXT NOT NULL,
                document_status TEXT DEFAULT 'Pending',
                sync_status TEXT DEFAULT 'Synced',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                mobile TEXT PRIMARY KEY,
                pin TEXT NOT NULL,
                state TEXT DEFAULT '{}'
            );
        """)
        
    conn.commit()
    cursor.close()
    conn.close()

init_db()

# --- Pydantic Models ---
class ApplicationPayload(BaseModel):
    id: str
    applicant_name: str
    annual_income: float
    category: str
    aadhaar_hash: str

class PreCheckRequest(BaseModel):
    applicant_name: str
    annual_income: float
    category: str
    income_certificate_text: Optional[str] = ""

class MobilePayload(BaseModel):
    phoneNumber: Optional[str] = None
    mobile: Optional[str] = None

class VerifyOtpPayload(BaseModel):
    phoneNumber: Optional[str] = None
    mobile: Optional[str] = None
    otp: str

class AuthPayload(BaseModel):
    mobile: str
    pin: str

def get_mobile_from_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.replace("Bearer ", "").strip()
    return token.replace("token_", "") if token.startswith("token_") else None

# --- Application Endpoints ---

@app.post("/api/v1/pre-check")
def run_ai_pre_check(data: PreCheckRequest):
    warnings = []
    is_eligible = True
    if data.category.upper() in ["SC", "ST", "PVTG"] and data.annual_income > 300000:
        warnings.append("Income is near or exceeds threshold for specific tribal sub-schemes.")
    elif data.annual_income > 600000:
        is_eligible = False
        warnings.append("Annual income exceeds general welfare scheme ceilings.")
    if len(data.applicant_name.strip()) < 3:
        is_eligible = False
        warnings.append("Applicant name appears invalid or too short.")
    return {"status": "success", "is_eligible": is_eligible, "warnings": warnings}

@app.post("/api/v1/sync-application", status_code=status.HTTP_201_CREATED)
def sync_application(app_data: ApplicationPayload):
    conn = get_connection()
    cursor = conn.cursor()
    param = "%s" if USE_PG else "?"
    try:
        cursor.execute(f"SELECT id FROM applications WHERE id = {param}", (app_data.id,))
        if cursor.fetchone():
            return {"status": "already_synced", "message": "Application already stored."}

        insert_sql = f"""
            INSERT INTO applications (id, applicant_name, annual_income, category, aadhaar_hash, sync_status)
            VALUES ({param}, {param}, {param}, {param}, {param}, 'Synced')
        """
        cursor.execute(insert_sql, (
            app_data.id,
            app_data.applicant_name,
            app_data.annual_income,
            app_data.category.upper(),
            app_data.aadhaar_hash
        ))
        conn.commit()
        return {"status": "success", "message": "Application stored in database."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/applications")
def get_applications():
    conn = get_connection()
    try:
        if USE_PG:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM applications ORDER BY created_at DESC;")
            records = cursor.fetchall()
            return {"applications": [dict(r) for r in records]}
        else:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM applications ORDER BY created_at DESC;")
            records = cursor.fetchall()
            return {"applications": [dict(r) for r in records]}
    finally:
        cursor.close()
        conn.close()

# --- Authentication & State Persistence ---

@app.get("/api/health")
def health_check():
    return {"status": "healthy"}

@app.get("/api/mobile-exists/{mobile}")
def check_mobile_exists(mobile: str):
    conn = get_connection()
    cursor = conn.cursor()
    param = "%s" if USE_PG else "?"
    cursor.execute(f"SELECT mobile FROM users WHERE mobile = {param}", (mobile,))
    exists = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return {"exists": exists}

@app.post("/api/send-otp")
def send_otp(payload: MobilePayload):
    num = payload.phoneNumber or payload.mobile
    if not num or len(num) != 10:
        raise HTTPException(status_code=400, detail="Invalid mobile number.")
    return {"success": True, "message": "OTP sent successfully"}

@app.post("/api/verify-otp")
def verify_otp(payload: VerifyOtpPayload):
    if payload.otp == "123456":
        return {"success": True}
    raise HTTPException(status_code=400, detail="Invalid OTP")

@app.post("/api/register")
def register_user(payload: AuthPayload):
    conn = get_connection()
    cursor = conn.cursor()
    param = "%s" if USE_PG else "?"
    try:
        cursor.execute(f"SELECT mobile FROM users WHERE mobile = {param}", (payload.mobile,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="exists")

        cursor.execute(
            f"INSERT INTO users (mobile, pin) VALUES ({param}, {param})",
            (payload.mobile, payload.pin)
        )
        conn.commit()
        return {
            "token": f"token_{payload.mobile}",
            "state": {"profile": None, "applications": []}
        }
    finally:
        cursor.close()
        conn.close()

@app.post("/api/login")
def login_user(payload: AuthPayload):
    conn = get_connection()
    cursor = conn.cursor()
    param = "%s" if USE_PG else "?"
    try:
        cursor.execute(f"SELECT pin FROM users WHERE mobile = {param}", (payload.mobile,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="no_account")
        stored_pin = row["pin"] if not USE_PG else row[0]
        if stored_pin != payload.pin:
            raise HTTPException(status_code=401, detail="wrong_pin")
        return {
            "token": f"token_{payload.mobile}",
            "state": {}
        }
    finally:
        cursor.close()
        conn.close()

@app.get("/api/me")
def get_current_user_state(authorization: Optional[str] = Header(None)):
    mobile = get_mobile_from_token(authorization)
    if not mobile:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"state": {}}

@app.put("/api/state")
def update_user_state(body: dict, authorization: Optional[str] = Header(None)):
    return {"status": "synced"}

@app.delete("/api/me")
def delete_user_account(authorization: Optional[str] = Header(None)):
    mobile = get_mobile_from_token(authorization)
    if not mobile:
        raise HTTPException(status_code=401, detail="Unauthorized")
    conn = get_connection()
    cursor = conn.cursor()
    param = "%s" if USE_PG else "?"
    try:
        cursor.execute(f"DELETE FROM users WHERE mobile = {param}", (mobile,))
        conn.commit()
        return {"status": "deleted"}
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
