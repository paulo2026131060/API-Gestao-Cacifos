# Túnel público para a API + Webhook do TTN
#
# Serve para receber os uplinks do TTN em redes que bloqueiam o MQTT
# (porta 8883), como a rede da universidade. O ngrok só usa a porta 443.
#
# Uso (com a API já a correr noutro terminal, na porta 8000):
#   python tunel_webhook.py          (ou: python tunel_webhook.py 8001)
#
# Precisa no .env:
#   TTN_WEBHOOK_SECRET   segredo que o TTN envia no cabeçalho X-Webhook-Secret
#   NGROK_AUTHTOKEN      token da conta gratuita do ngrok (dashboard.ngrok.com)
#   NGROK_DOMAIN         domínio fixo gratuito do ngrok (ex.: abc-xyz.ngrok-free.app)
# Opcional:
#   TTN_WEBHOOK_API_KEY  API key do TTN com permissão para editar as definições
#                        da application: o script cria/atualiza o webhook sozinho.
#
# Ctrl+C fecha o túnel.

import glob
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

from mqtt_ttn import TTN_APP_ID, TTN_SERVIDOR

load_dotenv()

PORTA_API = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
WEBHOOK_ID = "api-cacifos"
CAMINHO_UPLINK = "/ttn/uplink"

SEGREDO = os.getenv("TTN_WEBHOOK_SECRET", "")
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "")
# Aceita "abc.ngrok-free.app" ou "https://abc.ngrok-free.app/"
NGROK_DOMAIN = os.getenv("NGROK_DOMAIN", "").strip().removeprefix("https://").removeprefix("http://").strip("/")
CHAVE_GESTAO = os.getenv("TTN_WEBHOOK_API_KEY", "")


def encontrar_ngrok():
    caminho = shutil.which("ngrok")
    if caminho:
        return caminho
    # O winget instala aqui, mas o PATH só é atualizado em terminais novos
    local = os.environ.get("LOCALAPPDATA", "")
    candidatos = [os.path.join(local, "Microsoft", "WinGet", "Links", "ngrok.exe")]
    candidatos += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Ngrok.Ngrok_*", "ngrok.exe"))
    for candidato in candidatos:
        if os.path.exists(candidato):
            return candidato
    sys.exit("ngrok não encontrado. Instalar com: winget install Ngrok.Ngrok")


def configurar_webhook(url_publico):
    """Cria ou atualiza o webhook no TTN através da API do TTN."""
    pedido = {
        "webhook": {
            "ids": {"webhook_id": WEBHOOK_ID, "application_ids": {"application_id": TTN_APP_ID}},
            "base_url": url_publico,
            "format": "json",
            "headers": {"X-Webhook-Secret": SEGREDO},
            "uplink_message": {"path": CAMINHO_UPLINK},
        },
        "field_mask": {"paths": ["base_url", "format", "headers", "uplink_message"]},
    }
    req = urllib.request.Request(
        f"https://{TTN_SERVIDOR}/api/v3/as/webhooks/{TTN_APP_ID}/{WEBHOOK_ID}",
        data=json.dumps(pedido).encode(),
        method="PUT",
        headers={"Authorization": f"Bearer {CHAVE_GESTAO}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            pass
        print(f"[TÚNEL] Webhook '{WEBHOOK_ID}' do TTN aponta para {url_publico}{CAMINHO_UPLINK}", flush=True)
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode(errors="replace")[:300]
        print(f"[TÚNEL] O TTN recusou atualizar o webhook ({erro.code}): {detalhe}", flush=True)
        mostrar_passos_manuais(url_publico)
    except urllib.error.URLError as erro:
        print(f"[TÚNEL] Não foi possível contactar o TTN: {erro.reason}", flush=True)
        mostrar_passos_manuais(url_publico)


def mostrar_passos_manuais(url_publico):
    aviso = ("" if NGROK_DOMAIN else
             "  Sem NGROK_DOMAIN o endereço muda sempre que o túnel arranca: atualizar o Base URL de cada vez.\n")
    print(
        "\n[TÚNEL] Webhook no TTN (configurar uma vez):\n"
        f"  Applications → {TTN_APP_ID} → Integrations → Webhooks → Add webhook → Custom webhook\n"
        f"    Webhook ID:          {WEBHOOK_ID}\n"
        "    Webhook format:      JSON\n"
        f"    Base URL:            {url_publico}\n"
        "    Additional headers:  X-Webhook-Secret = <valor de TTN_WEBHOOK_SECRET no .env>\n"
        f"    Uplink message:      ativado, caminho {CAMINHO_UPLINK}\n" + aviso,
        flush=True,
    )


def main():
    if not SEGREDO:
        sys.exit("Falta TTN_WEBHOOK_SECRET no .env. Gerar com: "
                 "python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    if not NGROK_AUTHTOKEN:
        sys.exit("Falta NGROK_AUTHTOKEN no .env (criar conta grátis em https://dashboard.ngrok.com "
                 "e copiar o token em 'Your Authtoken').")

    comando = [encontrar_ngrok(), "http", str(PORTA_API), "--log=stdout", "--log-format=json"]
    if NGROK_DOMAIN:
        comando.append(f"--domain={NGROK_DOMAIN}")

    processo = subprocess.Popen(
        comando,
        env={**os.environ, "NGROK_AUTHTOKEN": NGROK_AUTHTOKEN},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    print(f"[TÚNEL] A abrir o túnel ngrok para http://localhost:{PORTA_API} ...", flush=True)

    url_publico = None
    try:
        for linha in processo.stdout:
            try:
                registo = json.loads(linha)
            except json.JSONDecodeError:
                print("[ngrok]", linha.rstrip(), flush=True)
                continue

            if url_publico is None and registo.get("msg") == "started tunnel":
                url_publico = registo["url"]
                print(f"[TÚNEL] Endereço público: {url_publico}", flush=True)
                if CHAVE_GESTAO:
                    configurar_webhook(url_publico)
                else:
                    mostrar_passos_manuais(url_publico)
                print("[TÚNEL] Túnel ativo. Ctrl+C para fechar.", flush=True)
            elif registo.get("lvl") in ("eror", "crit") or "err" in registo:
                print("[ngrok] ERRO:", registo.get("err") or registo.get("msg"), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        processo.terminate()
        print("[TÚNEL] Túnel fechado.", flush=True)
        if url_publico is None:
            sys.exit(1)


if __name__ == "__main__":
    main()
