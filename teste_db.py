from sqlalchemy import text
from database import engine

with engine.connect() as conn:
    versao = conn.execute(text("SELECT version()")).scalar()
    print("Ligado à base de dados!")
    print(versao)

    cacifos = conn.execute(text("SELECT COUNT(*) FROM cacifos")).scalar()
    pedidos = conn.execute(text("SELECT COUNT(*) FROM pedidos")).scalar()
    print(f"Cacifos: {cacifos} | Pedidos: {pedidos}")