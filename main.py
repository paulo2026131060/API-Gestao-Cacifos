# Locker Management API (FastAPI + Neon/PostgreSQL + TTN over MQTT)
#
# Start:
#   python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
#
# Simulate a TTN uplink to test the API without the XIAO:
#   Invoke-RestMethod -Uri http://localhost:8000/uplink -Method Post -ContentType "application/json" -Body '{"end_device_ids":{"device_id":"cacifo-01-abp","dev_eui":"70B3D57ED0079EC6"},"uplink_message":{"decoded_payload":{"porta":"ABERTA","trinco":"DESTRANCADO","alerta":false,"ocupado":false}}}'
#
# Request lifecycle:
#   Pending -> Approved -> Deposited -> Collected
#   Pending -> Rejected
#   (only Approved requests get a locker; Collected frees the locker)

import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import engine
from mqtt_ttn import iniciar_mqtt, parar_mqtt


RequestStatus = Literal["Pending", "Approved", "Rejected", "Deposited", "Collected"]
LockerFilter = Literal["free", "occupied", "alert"]


@asynccontextmanager
async def lifespan(app):
    iniciar_mqtt(process_uplink)   # connects to TTN when the API starts
    yield
    parar_mqtt()                   # disconnects when the API stops


app = FastAPI(title="Locker Management API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", include_in_schema=False)
def form_page():
    """Single page with the student form."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


# ---------------------------------------------------------------------------
# Manager authentication (API key)
# The Android app sends the header "X-API-Key: <MANAGER_API_KEY from .env>".
# Open routes (no key): the form page, POST /api/requests and /uplink.
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(api_key_header)):
    expected = os.environ.get("MANAGER_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="MANAGER_API_KEY is not configured on the server")
    if key is None or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


manager_only = [Depends(require_api_key)]


class NewRequest(BaseModel):
    student: str = Field(min_length=1, max_length=100, pattern=r"^\D+$")   # no digits
    student_number: str = Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")
    material: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_request(conn, request_id: int):
    """Returns the request or answers 404 if it does not exist."""
    row = conn.execute(
        text("""
            SELECT id, student, student_number, material, status, locker_id, created_at
            FROM requests
            WHERE id = :id
        """),
        {"id": request_id},
    ).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return row


def require_status(request, *allowed: str):
    """Answers 409 if the request is not in one of the allowed statuses."""
    if request["status"] not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(f"Action not possible: the request is '{request['status']}' "
                    f"(expected: {' or '.join(allowed)})"),
        )


def log_event(conn, event_type: str, source: str, request_id: int | None = None,
              locker_id: int | None = None, old_status: str | None = None,
              new_status: str | None = None, description: str | None = None):
    """Writes a row in the history (events). Uses the caller's transaction,
    so the action and its event are saved together (or neither is)."""
    conn.execute(
        text("""
            INSERT INTO events (event_type, source, request_id, locker_id,
                                old_status, new_status, description)
            VALUES (:event_type, :source, :request_id, :locker_id,
                    :old_status, :new_status, :description)
        """),
        {
            "event_type": event_type, "source": source,
            "request_id": request_id, "locker_id": locker_id,
            "old_status": old_status, "new_status": new_status,
            "description": description,
        },
    )


def set_status(conn, request, new_status: str):
    """Changes the request status (manager action) and records it in the history."""
    conn.execute(
        text("UPDATE requests SET status = :status WHERE id = :id"),
        {"status": new_status, "id": request["id"]},
    )
    log_event(conn, "status_changed", "manager",
              request_id=request["id"], locker_id=request["locker_id"],
              old_status=request["status"], new_status=new_status)


def free_locker(conn, locker_id: int | None):
    if locker_id is not None:
        conn.execute(
            text("UPDATE lockers SET occupied = false WHERE id = :id"),
            {"id": locker_id},
        )


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

@app.get("/api/dashboard", dependencies=manager_only)
def dashboard():
    with engine.connect() as conn:
        requests = conn.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'Pending')   AS pending,
                COUNT(*) FILTER (WHERE status = 'Approved')  AS approved,
                COUNT(*) FILTER (WHERE status = 'Approved'
                                   AND locker_id IS NULL)    AS approved_without_locker,
                COUNT(*) FILTER (WHERE status = 'Deposited') AS deposited
            FROM requests
        """)).mappings().one()

        lockers = conn.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE NOT occupied) AS free,
                COUNT(*) FILTER (WHERE occupied)     AS occupied,
                COUNT(*) FILTER (WHERE alert)        AS alerts
            FROM lockers
        """)).mappings().one()

    return {
        "pending_requests": requests["pending"],
        "approved_requests": requests["approved"],
        "approved_requests_without_locker": requests["approved_without_locker"],
        "deposited_requests": requests["deposited"],
        "free_lockers": lockers["free"],
        "occupied_lockers": lockers["occupied"],
        "lockers_with_alert": lockers["alerts"],
    }


# ---------------------------------------------------------------------------
# REQUESTS
# ---------------------------------------------------------------------------

@app.get("/api/requests", dependencies=manager_only)
def list_requests(status: RequestStatus | None = None, search: str | None = None):
    """Lists the requests (oldest first), filtered by status and searchable
    by student name, student number or request number."""
    sql = """
        SELECT id, student, student_number, material, status, locker_id, created_at
        FROM requests
        WHERE 1 = 1
    """
    params = {}

    if status:
        sql += " AND status = :status"
        params["status"] = status

    if search:
        sql += (" AND (student ILIKE :search OR student_number = :search_text"
                " OR CAST(id AS TEXT) = :search_text)")
        params["search"] = f"%{search.strip()}%"
        params["search_text"] = search.strip()

    sql += " ORDER BY created_at ASC, id ASC"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    return [dict(row) for row in rows]


@app.post("/api/requests", status_code=201)
def create_request(new: NewRequest):
    """Used by the website (student form). A request always starts as Pending."""
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO requests (student, student_number, material, status)
                VALUES (:student, :student_number, :material, 'Pending')
                RETURNING id, student, student_number, material, status, locker_id, created_at
            """),
            {
                "student": new.student.strip(),
                "student_number": new.student_number.strip(),
                "material": new.material.strip(),
            },
        ).mappings().one()

        log_event(conn, "request_created", "student_form",
                  request_id=row["id"], new_status="Pending")

    return dict(row)


@app.get("/api/requests/{request_id}", dependencies=manager_only)
def request_detail(request_id: int):
    """Request detail, including the state of the assigned locker (if any)."""
    with engine.connect() as conn:
        request = dict(get_request(conn, request_id))

        locker = None
        if request["locker_id"] is not None:
            locker = conn.execute(
                text("""
                    SELECT id, door_open, locked, has_item, alert,
                           last_communication
                    FROM lockers
                    WHERE id = :id
                """),
                {"id": request["locker_id"]},
            ).mappings().first()

    request["locker"] = dict(locker) if locker else None
    return request


@app.put("/api/requests/{request_id}/approve", dependencies=manager_only)
def approve(request_id: int):
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Pending")
        set_status(conn, request, "Approved")
    return {"success": True}


@app.put("/api/requests/{request_id}/reject", dependencies=manager_only)
def reject(request_id: int):
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Pending")
        set_status(conn, request, "Rejected")
    return {"success": True}


@app.put("/api/requests/{request_id}/deposited", dependencies=manager_only)
def deposited(request_id: int):
    """The item was placed in the assigned locker."""
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Approved")
        if request["locker_id"] is None:
            raise HTTPException(
                status_code=409,
                detail="Assign a locker to the request before marking it as deposited",
            )
        set_status(conn, request, "Deposited")
    return {"success": True}


@app.put("/api/requests/{request_id}/collected", dependencies=manager_only)
def collected(request_id: int):
    """The student collected the item: the locker becomes free.
    The locker_id stays on the request as history."""
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Deposited")
        set_status(conn, request, "Collected")
        free_locker(conn, request["locker_id"])
    return {"success": True}


@app.put("/api/requests/{request_id}/locker/{locker_id}", dependencies=manager_only)
def assign_locker(request_id: int, locker_id: int):
    """Assigns (or swaps) the locker of an Approved request."""
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Approved")

        if request["locker_id"] == locker_id:
            return {"success": True}

        exists = conn.execute(
            text("SELECT 1 FROM lockers WHERE id = :id"), {"id": locker_id}
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Locker not found")

        # Occupy the locker only if it is still free, in a single operation.
        # This way two managers at the same time never get the same locker.
        occupied_now = conn.execute(
            text("""
                UPDATE lockers
                SET occupied = true
                WHERE id = :id AND occupied = false
                RETURNING id
            """),
            {"id": locker_id},
        ).first()
        if occupied_now is None:
            raise HTTPException(status_code=409, detail="The locker is already occupied")

        free_locker(conn, request["locker_id"])   # locker swap

        conn.execute(
            text("UPDATE requests SET locker_id = :locker WHERE id = :id"),
            {"locker": locker_id, "id": request_id},
        )

        previous = request["locker_id"]
        log_event(conn, "locker_assigned", "manager",
                  request_id=request_id, locker_id=locker_id,
                  description=(f"Locker {previous} swapped for locker {locker_id}"
                               if previous is not None else None))

    return {"success": True}


@app.put("/api/requests/{request_id}/unassign", dependencies=manager_only)
def unassign_locker(request_id: int):
    """Removes the locker from an Approved request (before the deposit)."""
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Approved")

        free_locker(conn, request["locker_id"])
        conn.execute(
            text("UPDATE requests SET locker_id = NULL WHERE id = :id"),
            {"id": request_id},
        )

        if request["locker_id"] is not None:
            log_event(conn, "locker_unassigned", "manager",
                      request_id=request_id, locker_id=request["locker_id"])

    return {"success": True}


# ---------------------------------------------------------------------------
# LOCKERS
# ---------------------------------------------------------------------------

# Locker + active request (Approved or Deposited) and respective student
SQL_LOCKERS = """
    SELECT
        l.id,
        l.occupied,
        l.door_open,
        l.locked,
        l.has_item,
        l.alert,
        l.dev_eui,
        l.last_communication,
        r.id      AS request_id,
        r.student AS student,
        r.status  AS request_status
    FROM lockers l
    LEFT JOIN requests r
           ON r.locker_id = l.id
          AND r.status IN ('Approved', 'Deposited')
"""


@app.get("/api/lockers", dependencies=manager_only)
def list_lockers(status: LockerFilter | None = None):
    """All lockers, with an optional filter: free, occupied or alert."""
    sql = SQL_LOCKERS
    if status == "free":
        sql += " WHERE NOT l.occupied"
    elif status == "occupied":
        sql += " WHERE l.occupied"
    elif status == "alert":
        sql += " WHERE l.alert"
    sql += " ORDER BY l.id"

    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()

    return [dict(row) for row in rows]


@app.get("/api/lockers/free", dependencies=manager_only)
def free_lockers():
    """For the app's "Assign locker" dialog."""
    return list_lockers("free")


@app.get("/api/lockers/{locker_id}", dependencies=manager_only)
def locker_detail(locker_id: int):
    with engine.connect() as conn:
        locker = conn.execute(
            text(SQL_LOCKERS + " WHERE l.id = :id"), {"id": locker_id}
        ).mappings().first()

        if locker is None:
            raise HTTPException(status_code=404, detail="Locker not found")

        history = conn.execute(
            text("""
                SELECT id, student, student_number, material, status, created_at
                FROM requests
                WHERE locker_id = :id
                ORDER BY created_at DESC
                LIMIT 10
            """),
            {"id": locker_id},
        ).mappings().all()

    result = dict(locker)
    result["history"] = [dict(h) for h in history]
    return result


SQL_EVENTS = """
    SELECT id, event_date, event_type, source, request_id, locker_id,
           old_status, new_status, description
    FROM events
"""


@app.get("/api/requests/{request_id}/events", dependencies=manager_only)
def request_events(request_id: int):
    """History of a request, oldest first."""
    with engine.connect() as conn:
        get_request(conn, request_id)   # 404 if it does not exist
        rows = conn.execute(
            text(SQL_EVENTS + " WHERE request_id = :id ORDER BY event_date ASC, id ASC"),
            {"id": request_id},
        ).mappings().all()
    return [dict(row) for row in rows]


@app.get("/api/lockers/{locker_id}/events", dependencies=manager_only)
def locker_events(locker_id: int, limit: int = Query(50, ge=1, le=500)):
    """History of a locker (manager actions and sensor events), newest first."""
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM lockers WHERE id = :id"), {"id": locker_id}
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Locker not found")
        rows = conn.execute(
            text(SQL_EVENTS + " WHERE locker_id = :id ORDER BY event_date DESC, id DESC LIMIT :limit"),
            {"id": locker_id, "limit": limit},
        ).mappings().all()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# XIAO ESP32 (locker events: Wi-Fi or TTN over MQTT)
# The payload keys/values (porta, trinco, ABERTA...) are defined by the
# firmware, so they are kept as they are.
# ---------------------------------------------------------------------------

def process_uplink(data: dict):
    dev_eui = data["end_device_ids"]["dev_eui"]
    state = data["uplink_message"]["decoded_payload"]

    now = datetime.now().strftime("%H:%M:%S")
    line = (f"[{now}] {dev_eui}: door={state['porta']} "
            f"latch={state['trinco']}")
    if "ocupado" in state:
        line += " locker=" + ("OCCUPIED" if state["ocupado"] else "EMPTY")
    if state.get("alerta"):
        line += "  <-- ALERT: forced opening!"
    print(line, flush=True)

    door_open = state["porta"] == "ABERTA"
    alert = bool(state.get("alerta", False))

    with engine.begin() as conn:
        # Previous state (locked until the end of the transaction), to record
        # only real changes and not every repeated uplink.
        locker = conn.execute(
            text("SELECT id, door_open, alert FROM lockers WHERE dev_eui = :dev_eui FOR UPDATE"),
            {"dev_eui": dev_eui},
        ).mappings().first()

        if locker is None:
            print(f"  (no locker linked to device {dev_eui})", flush=True)
            return {"error": f"No locker linked to device {dev_eui}"}

        conn.execute(
            text("""
                UPDATE lockers
                SET door_open = :door_open,
                    locked = :locked,
                    alert = :alert,
                    has_item = :has_item,
                    last_communication = CURRENT_TIMESTAMP
                WHERE id = :id
            """),
            {
                "door_open": door_open,
                "locked": state["trinco"] == "TRANCADO",
                "alert": alert,
                "has_item": state.get("ocupado", False),
                "id": locker["id"],
            },
        )

        door_changed = locker["door_open"] is not None and door_open != locker["door_open"]
        alert_started = alert and not locker["alert"]

        if door_changed or alert_started:
            active_request = conn.execute(
                text("""
                    SELECT id FROM requests
                    WHERE locker_id = :id AND status IN ('Approved', 'Deposited')
                    LIMIT 1
                """),
                {"id": locker["id"]},
            ).scalar()

            if door_changed:
                log_event(conn, "door_opened" if door_open else "door_closed", "sensor",
                          request_id=active_request, locker_id=locker["id"])
            if alert_started:
                log_event(conn, "alert", "sensor",
                          request_id=active_request, locker_id=locker["id"],
                          description="Forced opening detected")

    return {"success": True}

#Teste para fazer um uplink simulado sem o XIAO, para testar a API:
@app.post("/uplink")
def receive_uplink(data: dict):
    return process_uplink(data)
