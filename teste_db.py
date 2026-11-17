from sqlalchemy import text
from database import engine

with engine.connect() as conn:
    versao = conn.execute(text("SELECT version()")).scalar()
    print("Ligado à base de dados!")
    print(versao)

    lockers = conn.execute(text("SELECT COUNT(*) FROM lockers")).scalar()
    requests = conn.execute(text("SELECT COUNT(*) FROM requests")).scalar()
    print(f"Lockers: {lockers} | Requests: {requests}")