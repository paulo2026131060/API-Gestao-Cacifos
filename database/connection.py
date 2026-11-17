import os

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()  # lê o ficheiro .env

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # testa a ligação antes de usar (a Neon fecha ligações paradas)
    pool_recycle=300,     # renova as ligações a cada 5 minutos
)