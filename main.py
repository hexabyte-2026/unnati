import os
import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="SIH Scholarship Offline-First Sync & Pre-Check API",
    version="1.0.0",
    description="Backend supporting zero-data-loss local form synchronization and AI pre-validation."
)

# Enable CORS for public website and mobile app access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "sih_scholarship.db"

# Initialize SQLite Database with Offline Sync Queue Support
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

# Pydantic Models for Validation
class ApplicationPayload(BaseModel):
    id: str = Field(..., description="Local UUID generated offline")
    applicant_name: str
    annual_income: float = Field(..., gt=0, description="Family annual income in INR")
    category: str = Field(..., description="Category like ST, SC, OBC, PVTG, General")
    aadhaar_hash: str = Field(..., description="Masked or hashed identifier for security")

class PreCheckRequest(BaseModel):
    applicant_name: str
    annual_income: float
    category: str
    income_certificate_text: Optional[str] = ""

# 1. AI Pre-Check Engine Endpoint (Validating rules locally/server-side)
@app.post("/api/v1/pre-check", status_code=status.HTTP_200_OK)
def run_ai_pre_check(data: PreCheckRequest):
    """
    Simulates the AI Pre-Check Engine to catch errors, income ceiling mismatches,
    and document discrepancies instantly before final submission.
    """
    warnings = []
    is_eligible = True

    # Example Rule 1: Income ceiling check based on typical welfare schemes (e.g., 2.5 Lakhs limit)
    if data.category.upper() in ["SC", "ST", "PVTG"] and data.annual_income > 300000:
        warnings.warn("Income is near or exceeds standard threshold for specific tribal sub-schemes.")
    elif data.annual_income > 600000:
        is_eligible = False
        warnings.append("Annual income exceeds general welfare scheme ceilings (6 Lakhs limit).")

    # Example Rule 2: Name pattern / Text anomaly checks
    if len(data.applicant_name.strip()) < 3:
        is_eligible = False
        warnings.append("Applicant name appears invalid or too short.")

    return {
        "status": "success",
        "is_eligible": is_eligible,
        "warnings": warnings,
        "message": "Pre-check completed successfully with zero data loss protection."
    }

# 2. Zero-Data-Loss Form Sync Endpoint
@app.post("/api/v1/sync-application", status_code=status.HTTP_201_CREATED)
def sync_application(app_data: ApplicationPayload):
    """
    Receives payloads queued offline from mobile/web clients when connection is restored.
    Ensures idempotent storage to prevent duplicate entries.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Check if record already exists (Idempotency)
        cursor.execute("SELECT id FROM applications WHERE id = ?", (app_data.id,))
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            return {"status": "already_synced", "message": "Application already exists on server."}

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
        return {"status": "success", "message": "Offline payload synced successfully."}
        
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)