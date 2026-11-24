-- Database schema of the Smart Lockers project (PostgreSQL / Neon)
-- Run once on an empty database (e.g. Neon SQL Editor)

CREATE TABLE lockers (
    id SERIAL PRIMARY KEY,
    occupied BOOLEAN NOT NULL DEFAULT FALSE,       -- assigned to a request
    door_open BOOLEAN NOT NULL DEFAULT FALSE,
    locked BOOLEAN NOT NULL DEFAULT TRUE,
    has_item BOOLEAN NOT NULL DEFAULT FALSE,       -- item detected inside
    alert BOOLEAN NOT NULL DEFAULT FALSE,
    dev_eui VARCHAR(16) UNIQUE,
    last_communication TIMESTAMP NULL
);

CREATE TABLE students (
    id SERIAL PRIMARY KEY,
    student_number VARCHAR(20) UNIQUE,
    name VARCHAR(100),
    nfc_uid VARCHAR(50)
);

CREATE TABLE requests (
    id SERIAL PRIMARY KEY,
    student VARCHAR(100) NOT NULL,
    student_number VARCHAR(20),
    material TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Pending',  -- Pending, Approved, Rejected, Deposited, Collected
    locker_id INTEGER REFERENCES lockers(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- History of requests and lockers (written by the API on every action / sensor change)
CREATE TABLE events (
    id          SERIAL PRIMARY KEY,
    event_date  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    event_type  VARCHAR(30) NOT NULL,  -- request_created, status_changed, locker_assigned,
                                       -- locker_unassigned, door_opened, door_closed, alert
    source      VARCHAR(20) NOT NULL,  -- student_form, manager, sensor
    request_id  INTEGER REFERENCES requests(id) ON DELETE CASCADE,
    locker_id   INTEGER REFERENCES lockers(id)  ON DELETE SET NULL,
    old_status  VARCHAR(20),           -- only in status_changed
    new_status  VARCHAR(20),
    description TEXT                   -- optional readable text
);
CREATE INDEX events_request_id_idx ON events (request_id);
CREATE INDEX events_locker_id_idx  ON events (locker_id);

-- Example lockers (locker 1 is the XIAO in ABP)
INSERT INTO lockers (dev_eui) VALUES ('70B3D57ED0079EC6');
INSERT INTO lockers (dev_eui) VALUES (NULL), (NULL);

-- A test request
INSERT INTO requests (student, student_number, material) VALUES ('Test Student', '12345', 'Laptop');

-- Migration from the old Portuguese schema (already applied on Neon):
-- ALTER TABLE pedidos RENAME TO requests;  ALTER TABLE cacifos RENAME TO lockers;
-- ALTER TABLE alunos RENAME TO students;   ALTER TABLE eventos RENAME TO events;
-- ... plus the column renames and status values (Pendente->Pending, etc.)
