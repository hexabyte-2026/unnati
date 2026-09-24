import os
import sqlite3
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="SIH Scholarship Sync & Pre-Check API",
    version="1.0.0",
    description="Backend supporting form synchronization and AI pre-validation."
)

# Enable CORS for public website access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "sih_scholarship.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Pydantic Models ---
class ApplicationPayload(BaseModel):
    id: str = Field(..., description="Application ID")
    applicant_name: str
    annual_income: float = Field(..., gt=0)
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

# In-memory storage for user session state
REGISTERED_USERS: Dict[str, Dict[str, Any]] = {}

def get_mobile_from_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.replace("Bearer ", "").strip()
    if token.startswith("token_"):
        return token.replace("token_", "")
    return None

# --- Application & Eligibility Endpoints ---
@app.post("/api/v1/pre-check", status_code=status.HTTP_200_OK)
def run_ai_pre_check(data: PreCheckRequest):
    warnings = []
    is_eligible = True

    if data.category.upper() in ["SC", "ST", "PVTG"] and data.annual_income > 300000:
        warnings.append("Income is near or exceeds standard threshold for specific tribal sub-schemes.")
    elif data.annual_income > 600000:
        is_eligible = False
        warnings.append("Annual income exceeds general welfare scheme ceilings (6 Lakhs limit).")

    if len(data.applicant_name.strip()) < 3:
        is_eligible = False
        warnings.append("Applicant name appears invalid or too short.")

    return {
        "status": "success",
        "is_eligible": is_eligible,
        "warnings": warnings,
        "message": "Pre-check completed successfully."
    }

@app.post("/api/v1/sync-application", status_code=status.HTTP_201_CREATED)
def sync_application(app_data: ApplicationPayload):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM applications WHERE id = ?", (app_data.id,))
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            return {"status": "already_synced", "message": "Application already stored."}

        cursor.execute("""
            INSERT INTO applications (id, applicant_name, annual_income, category, aadhaar_hash, sync_status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            app_data.id,
            app_data.applicant_name,
            app_data.annual_income,
            app_data.category.upper(),
            app_data.aadhaar_hash,
            "Synced"
        ))
        
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Application stored successfully in database."}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/applications", status_code=status.HTTP_200_OK)
def get_applications():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM applications ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return {"applications": [dict(row) for row in rows]}

# --- Frontend Auth & State Management Endpoints ---
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
    if payload.mobile in REGISTERED_USERS:
        raise HTTPException(status_code=400, detail="exists")
    REGISTERED_USERS[payload.mobile] = {
        "pin": payload.pin,
        "state": {"profile": None, "applications": []}
    }
    return {
        "token": f"token_{payload.mobile}",
        "state": REGISTERED_USERS[payload.mobile]["state"]
    }

@app.post("/api/login")
def login_user(payload: AuthPayload):
    user = REGISTERED_USERS.get(payload.mobile)
    if not user:
        raise HTTPException(status_code=404, detail="no_account")
    if user["pin"] != payload.pin:
        raise HTTPException(status_code=401, detail="wrong_pin")
    return {
        "token": f"token_{payload.mobile}",
        "state": user.get("state", {})
    }

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
