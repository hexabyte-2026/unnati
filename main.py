import os
import sqlite3
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Normalize database URL for psycopg2 if running on Render PostgreSQL
RAW_DB_URL = os.environ.get("DATABASE_URL")
if RAW_DB_URL and RAW_DB_URL.startswith("postgres://"):
    DATABASE_URL = RAW_DB_URL.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = RAW_DB_URL

USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor

app = FastAPI(
    title="SIH Scholarship Sync & Pre-Check API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "sih_scholarship.db"

def get_connection():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Applications Table
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
    
    # 2. Profiles Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            mobile TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            age INTEGER,
            gender TEXT,
            state TEXT,
            district TEXT,
            sub_caste TEXT,
            course_level TEXT,
            income NUMERIC,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

class ProfilePayload(BaseModel):
    mobile: str
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    subCaste: Optional[str] = None
    courseLevel: Optional[str] = None
    income: Optional[float] = 0.0

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

REGISTERED_USERS: Dict[str, Dict[str, Any]] = {}

def get_mobile_from_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.replace("Bearer ", "").strip()
    return token.replace("token_", "") if token.startswith("token_") else None

# ================= DATABASE ENDPOINTS =================

@app.post("/api/v1/save-profile", status_code=status.HTTP_200_OK)
def save_profile_to_db(payload: ProfilePayload):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        if USE_PG:
            query = """
                INSERT INTO profiles (mobile, name, age, gender, state, district, sub_caste, course_level, income, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (mobile) DO UPDATE SET
                    name = EXCLUDED.name,
                    age = EXCLUDED.age,
                    gender = EXCLUDED.gender,
                    state = EXCLUDED.state,
                    district = EXCLUDED.district,
                    sub_caste = EXCLUDED.sub_caste,
                    course_level = EXCLUDED.course_level,
                    income = EXCLUDED.income,
                    updated_at = CURRENT_TIMESTAMP;
            """
        else:
            query = """
                INSERT OR REPLACE INTO profiles (mobile, name, age, gender, state, district, sub_caste, course_level, income, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
            """
            
        cursor.execute(query, (
            payload.mobile,
            payload.name,
            payload.age,
            payload.gender,
            payload.state,
            payload.district,
            payload.subCaste,
            payload.courseLevel,
            payload.income
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "message": "Profile saved to database successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/profiles")
def get_all_profiles():
    conn = get_connection()
    if USE_PG:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM profiles ORDER BY updated_at DESC;")
        rows = cursor.fetchall()
        result = [dict(r) for r in rows]
    else:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM profiles ORDER BY updated_at DESC;")
        rows = cursor.fetchall()
        result = [dict(r) for r in rows]
    cursor.close()
    conn.close()
    return {"profiles": result}

@app.post("/api/v1/sync-application", status_code=status.HTTP_201_CREATED)
def sync_application(app_data: ApplicationPayload):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        param = "%s" if USE_PG else "?"
        cursor.execute(f"SELECT id FROM applications WHERE id = {param}", (app_data.id,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return {"status": "already_synced", "message": "Application already stored."}

        insert_query = f"""
            INSERT INTO applications (id, applicant_name, annual_income, category, aadhaar_hash, sync_status)
            VALUES ({param}, {param}, {param}, {param}, {param}, 'Synced')
        """
        cursor.execute(insert_query, (
            app_data.id,
            app_data.applicant_name,
            app_data.annual_income,
            app_data.category.upper(),
            app_data.aadhaar_hash
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "success", "message": "Application saved to database."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/applications")
def get_applications():
    conn = get_connection()
    if USE_PG:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM applications ORDER BY created_at DESC;")
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
    else:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM applications ORDER BY created_at DESC")
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
    cursor.close()
    conn.close()
    return {"applications": result}

# ================= AUTH & VERIFICATION ENDPOINTS =================

@app.get("/api/health")
def health_check():
    return {"status": "healthy"}

@app.get("/api/mobile-exists/{mobile}")
def check_mobile_exists(mobile: str):
    return {"exists": mobile in REGISTERED_USERS}

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
    REGISTERED_USERS[payload.mobile] = {"pin": payload.pin, "state": {}}
    return {"token": f"token_{payload.mobile}", "state": {"profile": None, "applications": []}}

@app.post("/api/login")
def login_user(payload: AuthPayload):
    user = REGISTERED_USERS.get(payload.mobile)
    if not user:
        raise HTTPException(status_code=404, detail="no_account")
    if user["pin"] != payload.pin:
        raise HTTPException(status_code=401, detail="wrong_pin")
    return {"token": f"token_{payload.mobile}", "state": user.get("state", {})}

@app.get("/api/me")
def get_current_user_state(authorization: Optional[str] = Header(None)):
    mobile = get_mobile_from_token(authorization)
    if not mobile or mobile not in REGISTERED_USERS:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"state": REGISTERED_USERS[mobile].get("state", {})}

@app.put("/api/state")
def update_user_state(body: dict, authorization: Optional[str] = Header(None)):
    mobile = get_mobile_from_token(authorization)
    if mobile and mobile in REGISTERED_USERS:
        REGISTERED_USERS[mobile]["state"] = body
    return {"status": "synced"}

@app.delete("/api/me")
def delete_user_account(authorization: Optional[str] = Header(None)):
    mobile = get_mobile_from_token(authorization)
    if mobile and mobile in REGISTERED_USERS:
        del REGISTERED_USERS[mobile]
        return {"status": "deleted"}
    raise HTTPException(status_code=401, detail="Unauthorized")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
