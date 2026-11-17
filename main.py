# python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Query
from sqlalchemy import text

from database import engine
from mqtt_ttn import iniciar_mqtt, parar_mqtt


@asynccontextmanager
async def lifespan(app):
    iniciar_mqtt(processar_uplink)   # liga ao TTN quando a API arranca
    yield
    parar_mqtt()                     # desliga quando a API para


app = FastAPI(lifespan=lifespan)

@app.get("/api/dashboard")
def dashboard():

    with engine.connect() as conn:

        pendentes = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM pedidos
                WHERE estado = 'Pendente'
            """)
        ).scalar()

        preparados = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM pedidos
                WHERE estado = 'Preparado'
            """)
        ).scalar()

        aprovados = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM pedidos
                WHERE estado = 'Aprovado'
            """)
        ).scalar()

        cacifos_livres = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM cacifos
                WHERE ocupado = false
            """)
        ).scalar()

        cacifos_ocupados = conn.execute(
            text("""
                SELECT COUNT(*)
                FROM cacifos
                WHERE ocupado = true
            """)
        ).scalar()

    return {
        "pedidos_pendentes": pendentes,
        "pedidos_preparados": preparados,
        "pedidos_aprovados": aprovados,
        "cacifos_livres": cacifos_livres,
        "cacifos_ocupados": cacifos_ocupados
    }

@app.get("/api/pedidos")
def pedidos(estado: str | None = Query(None)):

    with engine.connect() as conn:

        if estado:

            result = conn.execute(
                text("""
                    SELECT
                        id,
                        aluno,
                        material,
                        estado
                    FROM pedidos
                    WHERE estado = :estado
                    ORDER BY id ASC
                """),
                {"estado": estado}
            )

        else:

            result = conn.execute(
                text("""
                    SELECT
                        id,
                        aluno,
                        material,
                        estado
                    FROM pedidos
                    ORDER BY id ASC
                """)
            )

        pedidos = []

        for row in result:

            pedidos.append(
                {
                    "id": row.id,
                    "aluno": row.aluno,
                    "material": row.material,
                    "estado": row.estado
                }
            )

        return pedidos


@app.get("/api/pedidos/{pedido_id}")
def get_pedido(pedido_id: int):

    with engine.connect() as conn:

        result = conn.execute(
            text("""
                SELECT
                    id,
                    aluno,
                    material,
                    estado,
                    cacifo_id
                FROM pedidos
                WHERE id = :id
            """),
            {"id": pedido_id}
        )

        row = result.mappings().first()

        if row is None:
            return {
                "erro": "Pedido não encontrado"
            }

        return {
            "id": row["id"],
            "aluno": row["aluno"],
            "material": row["material"],
            "estado": row["estado"],
            "cacifo_id": row["cacifo_id"]
        }


@app.put("/api/pedidos/{pedido_id}/aprovar")
def aprovar(pedido_id: int):
    with engine.connect() as conn:

        conn.execute(
            text("""
                UPDATE pedidos
                SET estado='Aprovado'
                WHERE id=:id
            """),
            {"id": pedido_id}
        )

        conn.commit()

    return {"success": True}


@app.put("/api/pedidos/{pedido_id}/rejeitar")
def rejeitar(pedido_id: int):

    with engine.connect() as conn:

        conn.execute(
            text("""
                UPDATE pedidos
                SET estado='Rejeitado'
                WHERE id=:id
            """),
            {"id": pedido_id}
        )

        conn.commit()

    return {"success": True}


@app.put("/api/pedidos/{pedido_id}/preparado")
def preparado(pedido_id: int):

    with engine.connect() as conn:

        conn.execute(
            text("""
                UPDATE pedidos
                SET estado='Preparado'
                WHERE id=:id
            """),
            {"id": pedido_id}
        )

        conn.commit()

    return {"success": True}


####################################################################################
############################# CACIFOS ############################
####################################################################################
@app.get("/api/cacifos")
def cacifos():

    with engine.connect() as conn:

        result = conn.execute(
            text("""
                SELECT
                    c.id,
                    c.ocupado,
                    c.porta_aberta
                FROM cacifos c
                WHERE c.ocupado = false
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pedidos p
                      WHERE p.cacifo_id = c.id
                  )
                ORDER BY c.id
            """)
        )

        lista = []

        for row in result:
            lista.append({
                "id": row.id,
                "ocupado": row.ocupado,
                "porta_aberta": row.porta_aberta
            })

        return lista
    
@app.put("/api/pedidos/{pedido_id}/cacifo/{cacifo_id}")
def associar_cacifo(pedido_id: int, cacifo_id: int):

    with engine.connect() as conn:

        # Obter o cacifo atual do pedido
        result = conn.execute(
            text("""
                SELECT cacifo_id
                FROM pedidos
                WHERE id = :pedido
            """),
            {"pedido": pedido_id}
        )

        pedido = result.mappings().first()

        if pedido is None:
            return {"erro": "Pedido não existe"}

        cacifo_antigo = pedido["cacifo_id"]

        # Verificar se o novo cacifo existe
        result = conn.execute(
            text("""
                SELECT id
                FROM cacifos
                WHERE id = :cacifo
            """),
            {"cacifo": cacifo_id}
        )

        cacifo = result.mappings().first()

        if cacifo is None:
            return {"erro": "Cacifo não existe"}

        # Verificar se já está associado a outro pedido
        result = conn.execute(
            text("""
                SELECT id
                FROM pedidos
                WHERE cacifo_id = :cacifo
                  AND id <> :pedido
            """),
            {
                "cacifo": cacifo_id,
                "pedido": pedido_id
            }
        )

        if result.first():
            return {"erro": "Cacifo já associado"}

        # Libertar o cacifo antigo
        if cacifo_antigo is not None and cacifo_antigo != cacifo_id:

            conn.execute(
                text("""
                    UPDATE cacifos
                    SET ocupado = false
                    WHERE id = :cacifo
                """),
                {"cacifo": cacifo_antigo}
            )

        # Associar novo cacifo ao pedido
        conn.execute(
            text("""
                UPDATE pedidos
                SET cacifo_id = :cacifo
                WHERE id = :pedido
            """),
            {
                "pedido": pedido_id,
                "cacifo": cacifo_id
            }
        )

        # Marcar novo cacifo como ocupado
        conn.execute(
            text("""
                UPDATE cacifos
                SET ocupado = true
                WHERE id = :cacifo
            """),
            {"cacifo": cacifo_id}
        )

        conn.commit()

    return {"success": True}

@app.put("/api/pedidos/{pedido_id}/desassociar")
def desassociar_cacifo(pedido_id: int):

    with engine.connect() as conn:

        # Descobrir qual o cacifo associado ao pedido
        result = conn.execute(
            text("""
                SELECT cacifo_id
                FROM pedidos
                WHERE id = :pedido
            """),
            {"pedido": pedido_id}
        )

        row = result.mappings().first()

        if row is None:
            return {"erro": "Pedido não encontrado"}

        cacifo_id = row["cacifo_id"]

        # Remover associação do pedido
        conn.execute(
            text("""
                UPDATE pedidos
                SET cacifo_id = NULL
                WHERE id = :pedido
            """),
            {"pedido": pedido_id}
        )

        # Libertar o cacifo
        if cacifo_id is not None:
            conn.execute(
                text("""
                    UPDATE cacifos
                    SET ocupado = false
                    WHERE id = :cacifo
                """),
                {"cacifo": cacifo_id}
            )

        conn.commit()

    return {
        "success": True
    }

####################################################################################
############################# XIAO ESP 32 ############################
####################################################################################

def processar_uplink(dados: dict):

    dev_eui = dados["end_device_ids"]["dev_eui"]
    estado = dados["uplink_message"]["decoded_payload"]

    hora = datetime.now().strftime("%H:%M:%S")
    linha = (f"[{hora}] {dev_eui}: porta={estado['porta']} "
             f"trinco={estado['trinco']}")
    if "ocupado" in estado:
        linha += " cacifo=" + ("OCUPADO" if estado["ocupado"] else "VAZIO")
    if estado.get("alerta"):
        linha += "  <-- ALERTA: abertura forçada!"
    print(linha, flush=True)

    with engine.connect() as conn:

        result = conn.execute(
            text("""
                UPDATE cacifos
                SET porta_aberta = :porta_aberta,
                    trancado = :trancado,
                    alerta = :alerta,
                    tem_encomenda = :tem_encomenda,
                    ultima_comunicacao = CURRENT_TIMESTAMP
                WHERE dev_eui = :dev_eui
            """),
            {
                "porta_aberta": estado["porta"] == "ABERTA",
                "trancado": estado["trinco"] == "TRANCADO",
                "alerta": estado.get("alerta", False),
                "tem_encomenda": estado.get("ocupado", False),
                "dev_eui": dev_eui
            }
        )

        conn.commit()

    if result.rowcount == 0:
        print(f"  (nenhum cacifo associado ao dispositivo {dev_eui})", flush=True)
        return {"erro": f"Nenhum cacifo associado ao dispositivo {dev_eui}"}

    return {"success": True}


@app.post("/uplink")
def receber_uplink(dados: dict):
    return processar_uplink(dados)
