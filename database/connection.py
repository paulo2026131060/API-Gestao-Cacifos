import os
import socket

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from .neon_websocket import iniciar_proxy

load_dotenv()  # lê o ficheiro .env

DATABASE_URL = os.environ["DATABASE_URL"]

# Como ligar à Neon:
#   tcp       -> ligação normal na porta 5432
#   websocket -> PostgreSQL dentro de wss:// na porta 443 (redes que bloqueiam a 5432)
#   auto      -> tenta a 5432; se estiver bloqueada, usa o websocket (por omissão)
DATABASE_TRANSPORT = os.getenv("DATABASE_TRANSPORT", "auto").lower()


def _porta_5432_aberta(host: str) -> bool:
    try:
        with socket.create_connection((host, 5432), timeout=3):
            return True
    except OSError:
        return False


def _url_e_argumentos():
    url = make_url(DATABASE_URL)

    usar_websocket = DATABASE_TRANSPORT == "websocket" or (
        DATABASE_TRANSPORT == "auto" and not _porta_5432_aberta(url.host)
    )
    if not usar_websocket:
        print("[DB] Ligação à Neon por TCP (porta 5432)", flush=True)
        return url, {}

    print("[DB] Porta 5432 indisponível: ligação à Neon por WebSocket (wss://, porta 443)", flush=True)
    porta_local = iniciar_proxy(url.host)

    query = dict(url.query)
    query["sslmode"] = "disable"         # o TLS é feito pelo wss://
    query.pop("channel_binding", None)   # só funciona com TLS direto no PostgreSQL
    url_local = url.set(host="127.0.0.1", port=porta_local, query=query)

    # A Neon identifica a base de dados pelo endpoint (1.ª parte do host, ex.: ep-xxxx)
    endpoint = url.host.split(".")[0]
    return url_local, {"options": f"endpoint={endpoint}"}


_url, _connect_args = _url_e_argumentos()

engine = create_engine(
    _url,
    connect_args=_connect_args,
    pool_pre_ping=True,   # testa a ligação antes de usar (a Neon fecha ligações paradas)
    pool_recycle=300,     # renova as ligações a cada 5 minutos
)
