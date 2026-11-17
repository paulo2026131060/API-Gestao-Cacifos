# Ligação MQTT ao The Things Network (TTN)
# Recebe os uplinks de todos os dispositivos da application e entrega-os
# à mesma função que trata o endpoint /uplink da API.

import json
import os

import paho.mqtt.client as mqtt

# ===================== CONFIGURAR =====================
TTN_SERVIDOR = "eu1.cloud.thethings.network"
TTN_PORTA = 8883  # ligação segura (TLS)
TTN_APP_ID = "projeto-redes-de-sensores"
TTN_UTILIZADOR = f"{TTN_APP_ID}@ttn"
# A API key do TTN (começa por NNSXS.). Pode vir de uma variável de ambiente.
TTN_API_KEY = os.getenv("TTN_API_KEY", "")
# ======================================================

# Tópico com os uplinks de todos os dispositivos da application
TOPICO_UPLINKS = f"v3/{TTN_UTILIZADOR}/devices/+/up"

_cliente = None


def iniciar_mqtt(processar_uplink):
    """Liga ao TTN e chama processar_uplink(dados) para cada uplink recebido."""
    global _cliente

    def ao_ligar(cliente, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("[MQTT] Ligado ao TTN. A subscrever os uplinks...", flush=True)
            cliente.subscribe(TOPICO_UPLINKS)
        else:
            print(f"[MQTT] Falha na ligação ao TTN: {reason_code}", flush=True)

    def ao_receber(cliente, userdata, msg):
        try:
            dados = json.loads(msg.payload)
            print(json.dumps(dados, indent=2), flush=True)
            # Ignora mensagens sem dados da aplicação (ex.: só comandos MAC)
            if "decoded_payload" not in dados.get("uplink_message", {}):
                return
            processar_uplink(dados)
        except Exception as erro:
            print(f"[MQTT] Erro ao processar mensagem de {msg.topic}: {erro}", flush=True)

    _cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    _cliente.username_pw_set(TTN_UTILIZADOR, TTN_API_KEY)
    _cliente.tls_set()
    _cliente.on_connect = ao_ligar
    _cliente.on_message = ao_receber

    _cliente.connect_async(TTN_SERVIDOR, TTN_PORTA)
    _cliente.loop_start()  # corre em segundo plano, ao lado da API


def parar_mqtt():
    if _cliente is not None:
        _cliente.loop_stop()
        _cliente.disconnect()