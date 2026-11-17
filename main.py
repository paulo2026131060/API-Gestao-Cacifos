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

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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


@app.get("/", include_in_schema=False)
def form_page():
    """Single page with the student form."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


class NewRequest(BaseModel):
    student: str = Field(min_length=1, max_length=100)
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


def set_status(conn, request_id: int, new_status: str):
    conn.execute(
        text("UPDATE requests SET status = :status WHERE id = :id"),
        {"status": new_status, "id": request_id},
    )


def free_locker(conn, locker_id: int | None):
    if locker_id is not None:
        conn.execute(
            text("UPDATE lockers SET occupied = false WHERE id = :id"),
            {"id": locker_id},
        )


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

@app.get("/api/dashboard")
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

@app.get("/api/requests")
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

    return dict(row)


@app.get("/api/requests/{request_id}")
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


@app.put("/api/requests/{request_id}/approve")
def approve(request_id: int):
    with engine.begin() as conn:
        require_status(get_request(conn, request_id), "Pending")
        set_status(conn, request_id, "Approved")
    return {"success": True}


@app.put("/api/requests/{request_id}/reject")
def reject(request_id: int):
    with engine.begin() as conn:
        require_status(get_request(conn, request_id), "Pending")
        set_status(conn, request_id, "Rejected")
    return {"success": True}


@app.put("/api/requests/{request_id}/deposited")
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
        set_status(conn, request_id, "Deposited")
    return {"success": True}


@app.put("/api/requests/{request_id}/collected")
def collected(request_id: int):
    """The student collected the item: the locker becomes free.
    The locker_id stays on the request as history."""
    with engine.begin() as conn:
        request = get_request(conn, request_id)
        require_status(request, "Deposited")
        set_status(conn, request_id, "Collected")
        free_locker(conn, request["locker_id"])
    return {"success": True}


@app.put("/api/requests/{request_id}/locker/{locker_id}")
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

    return {"success": True}


@app.put("/api/requests/{request_id}/unassign")
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


@app.get("/api/lockers")
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


@app.get("/api/lockers/free")
def free_lockers():
    """For the app's "Assign locker" dialog."""
    return list_lockers("free")


@app.get("/api/lockers/{locker_id}")
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

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE lockers
                SET door_open = :door_open,
                    locked = :locked,
                    alert = :alert,
                    has_item = :has_item,
                    last_communication = CURRENT_TIMESTAMP
                WHERE dev_eui = :dev_eui
            """),
            {
                "door_open": state["porta"] == "ABERTA",
                "locked": state["trinco"] == "TRANCADO",
                "alert": state.get("alerta", False),
                "has_item": state.get("ocupado", False),
                "dev_eui": dev_eui,
            },
        )

    if result.rowcount == 0:
        print(f"  (no locker linked to device {dev_eui})", flush=True)
        return {"error": f"No locker linked to device {dev_eui}"}

    return {"success": True}


@app.post("/uplink")
def receive_uplink(data: dict):
    return process_uplink(data)
