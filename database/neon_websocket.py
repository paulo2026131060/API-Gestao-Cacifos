# Ligação à Neon por WebSocket seguro (wss://, porta 443)
#
# Algumas redes (ex.: a da universidade) bloqueiam a porta 5432 do PostgreSQL.
# A Neon aceita o mesmo protocolo do PostgreSQL dentro de um WebSocket em
# wss://<host>/v2 (é o que o driver "serverless" oficial da Neon usa).
#
# Este módulo abre um pequeno proxy local (127.0.0.1, porta aleatória): o
# psycopg liga-se a ele como se fosse um PostgreSQL normal e cada ligação é
# reencaminhada, byte a byte, por um WebSocket até à Neon. Assim o SQLAlchemy,
# as queries e as transações ficam exatamente iguais.
# A encriptação é feita pelo wss:// (TLS), por isso a ligação local ao proxy
# usa sslmode=disable.

import socket
import threading

from websockets.sync.client import connect as ws_connect

_porta_local = None
_lock = threading.Lock()


def _reencaminhar(cliente: socket.socket, host_neon: str):
    """Liga uma ligação do psycopg a um WebSocket novo até à Neon."""
    try:
        ws = ws_connect(f"wss://{host_neon}/v2", max_size=None, compression=None, open_timeout=15)
    except Exception as erro:
        print(f"[DB] Falha ao abrir o WebSocket para a Neon: {erro}", flush=True)
        cliente.close()
        return

    def psycopg_para_neon():
        try:
            while dados := cliente.recv(65536):
                ws.send(dados)
        except OSError:
            pass
        finally:
            ws.close()

    threading.Thread(target=psycopg_para_neon, daemon=True).start()

    try:
        for mensagem in ws:   # neon -> psycopg
            cliente.sendall(mensagem if isinstance(mensagem, bytes) else mensagem.encode())
    except Exception:
        pass
    finally:
        cliente.close()


def iniciar_proxy(host_neon: str) -> int:
    """Arranca o proxy (só uma vez) e devolve a porta local onde ouve."""
    global _porta_local
    with _lock:
        if _porta_local is not None:
            return _porta_local

        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        servidor.bind(("127.0.0.1", 0))   # só aceita ligações deste PC
        servidor.listen()
        _porta_local = servidor.getsockname()[1]

        def aceitar():
            while True:
                cliente, _ = servidor.accept()
                threading.Thread(target=_reencaminhar, args=(cliente, host_neon), daemon=True).start()

        threading.Thread(target=aceitar, daemon=True).start()
        return _porta_local
