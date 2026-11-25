# Ligação MQTT ao ChirpStack (servidor LoRaWAN do IPS)
# Recebe os uplinks das applications do ChirpStack, converte-os para o mesmo
# formato do TTN e entrega-os à mesma função que trata os uplinks do TTN.
#
# Só arranca se CHIRPSTACK_MQTT_HOST estiver definido no .env.
#
# .env:
#   CHIRPSTACK_MQTT_HOST      endereço do broker MQTT do ChirpStack (obrigatório)
#   CHIRPSTACK_MQTT_PORT      porta (por omissão 1883; com TLS costuma ser 8883)
#   CHIRPSTACK_MQTT_TLS       true/false (por omissão false)
#   CHIRPSTACK_MQTT_USER      utilizador do broker (se o broker pedir)
#   CHIRPSTACK_MQTT_PASSWORD  password do broker (se o broker pedir)
#   CHIRPSTACK_MQTT_TOPIC     por omissão application/+/device/+/event/up
#                             (todas as applications; restringir com o ID da application)

import base64
import json
import os
import re

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# Aceita "193.137.46.43" ou o URL da consola ("http://193.137.46.43:8080/"):
# fica só o endereço, porque o MQTT usa a sua própria porta (CHIRPSTACK_MQTT_PORT).
CS_SERVIDOR = re.sub(r"^[a-z]+://", "", os.getenv("CHIRPSTACK_MQTT_HOST", "").strip()).split("/")[0].split(":")[0]
CS_PORTA = int(os.getenv("CHIRPSTACK_MQTT_PORT", "1883"))
CS_TLS = os.getenv("CHIRPSTACK_MQTT_TLS", "false").lower() in ("1", "true", "sim", "yes")
CS_UTILIZADOR = os.getenv("CHIRPSTACK_MQTT_USER", "")
CS_PASSWORD = os.getenv("CHIRPSTACK_MQTT_PASSWORD", "")
CS_TOPICO = os.getenv("CHIRPSTACK_MQTT_TOPIC", "application/+/device/+/event/up")

_cliente = None


def _dev_eui_em_hex(valor: str) -> str:
    """O ChirpStack v4 envia o DevEUI em hex; algumas versões da v3 em base64."""
    if re.fullmatch(r"[0-9a-fA-F]{16}", valor):
        return valor.upper()
    return base64.b64decode(valor).hex().upper()


def converter_para_formato_ttn(dados: dict) -> dict | None:
    """Converte um uplink do ChirpStack (v4 ou v3) para o formato do TTN que a
    API já entende. Devolve None se a mensagem não tiver dados descodificados."""
    if "deviceInfo" in dados:                       # ChirpStack v4
        info = dados["deviceInfo"]
        dev_eui, nome = info["devEui"], info.get("deviceName")
    else:                                           # ChirpStack v3
        dev_eui, nome = dados["devEUI"], dados.get("deviceName")

    descodificado = dados.get("object")
    if not descodificado:
        return None   # sem codec no device profile, ou uplink sem dados

    return {
        "end_device_ids": {"device_id": nome, "dev_eui": _dev_eui_em_hex(dev_eui)},
        "uplink_message": {"decoded_payload": descodificado},
    }


def iniciar_mqtt_chirpstack(processar_uplink):
    """Liga ao broker do ChirpStack e chama processar_uplink(dados) para cada uplink."""
    global _cliente

    if not CS_SERVIDOR:
        print("[ChirpStack] CHIRPSTACK_MQTT_HOST não definido no .env: integração desligada.", flush=True)
        return

    aviso_falha = {"mostrado": False}

    def ao_ligar(cliente, userdata, flags, reason_code, properties):
        if reason_code == 0:
            aviso_falha["mostrado"] = False
            print(f"[ChirpStack] Ligado a {CS_SERVIDOR}:{CS_PORTA}. A subscrever {CS_TOPICO}", flush=True)
            cliente.subscribe(CS_TOPICO)
        else:
            print(f"[ChirpStack] Falha na ligação: {reason_code}", flush=True)

    def ao_subscrever(cliente, userdata, mid, reason_codes, properties):
        # O broker pode aceitar a ligação mas recusar o tópico (sem permissão)
        if any(rc.is_failure for rc in reason_codes):
            print(f"[ChirpStack] O broker RECUSOU a subscrição de {CS_TOPICO}: sem permissão para "
                  "esse tópico. Usar o ID da application em CHIRPSTACK_MQTT_TOPIC.", flush=True)
        else:
            print("[ChirpStack] Subscrição aceite: à espera de uplinks.", flush=True)

    def ao_falhar_ligacao(cliente, userdata):
        if not aviso_falha["mostrado"]:
            aviso_falha["mostrado"] = True
            print(f"[ChirpStack] Não foi possível ligar a {CS_SERVIDOR}:{CS_PORTA}. "
                  "A rede pode estar a bloquear a porta. A tentar de novo em segundo plano...",
                  flush=True)

    def ao_receber(cliente, userdata, msg):
        try:
            dados = json.loads(msg.payload)
            print(f"[ChirpStack] Uplink em {msg.topic}", flush=True)
            convertido = converter_para_formato_ttn(dados)
            if convertido is None:
                print("[ChirpStack] Uplink sem 'object' (falta o codec no device profile?): ignorado",
                      flush=True)
                return
            processar_uplink(convertido)
        except Exception as erro:
            print(f"[ChirpStack] Erro ao processar mensagem de {msg.topic}: {erro}", flush=True)

    _cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if CS_UTILIZADOR:
        _cliente.username_pw_set(CS_UTILIZADOR, CS_PASSWORD)
    if CS_TLS:
        _cliente.tls_set()
    _cliente.on_connect = ao_ligar
    _cliente.on_connect_fail = ao_falhar_ligacao
    _cliente.on_subscribe = ao_subscrever
    _cliente.on_message = ao_receber

    _cliente.connect_async(CS_SERVIDOR, CS_PORTA)
    _cliente.loop_start()


def parar_mqtt_chirpstack():
    if _cliente is not None:
        _cliente.loop_stop()
        _cliente.disconnect()


# ---------------------------------------------------------------------------
# DOWNLINKS (API -> ChirpStack -> gateway -> XIAO)
# ---------------------------------------------------------------------------

class DownlinkError(Exception):
    """O downlink não pôde ser entregue ao ChirpStack."""


def _id_da_application() -> str:
    """ID da application: CHIRPSTACK_APPLICATION_ID ou o que está no tópico."""
    explicito = os.getenv("CHIRPSTACK_APPLICATION_ID", "").strip()
    if explicito:
        return explicito
    encontrado = re.match(r"^application/([^/+#]+)/", CS_TOPICO)
    if not encontrado:
        raise DownlinkError("Falta o ID da application: definir CHIRPSTACK_MQTT_TOPIC="
                            "application/<ID>/device/+/event/up (ou CHIRPSTACK_APPLICATION_ID) no .env")
    return encontrado.group(1)


def enviar_downlink(dev_eui: str, dados: bytes, fport: int = 1):
    """Põe um downlink na fila do ChirpStack. O XIAO é classe A: só o recebe
    logo a seguir ao próximo uplink que enviar."""
    if _cliente is None:
        raise DownlinkError("Integração ChirpStack desligada (CHIRPSTACK_MQTT_HOST não definido)")
    if not _cliente.is_connected():
        raise DownlinkError(f"Sem ligação ao broker do ChirpStack ({CS_SERVIDOR}:{CS_PORTA})")

    dev_eui = dev_eui.lower()
    topico = f"application/{_id_da_application()}/device/{dev_eui}/command/down"
    mensagem = {
        "devEui": dev_eui,            # obrigatório na v4 (a v3 ignora)
        "confirmed": False,
        "fPort": fport,
        "data": base64.b64encode(dados).decode(),
    }

    info = _cliente.publish(topico, json.dumps(mensagem), qos=1)
    try:
        info.wait_for_publish(timeout=5)
    except (RuntimeError, ValueError) as erro:
        raise DownlinkError(f"Falha ao publicar o downlink: {erro}") from erro
    if not info.is_published():
        raise DownlinkError("O broker do ChirpStack não confirmou o downlink a tempo")

    print(f"[ChirpStack] Downlink {dados.hex()} (FPort {fport}) em fila para {dev_eui.upper()}", flush=True)
