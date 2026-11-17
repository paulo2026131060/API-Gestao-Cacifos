# Recetor de teste: recebe os eventos do cacifo (formato de uplink do TTN)
# Corre com:  python receptor.py
# Não precisa de instalar bibliotecas.

from http.server import BaseHTTPRequestHandler, HTTPServer
import datetime
import json

PORTA = 8081


class Receptor(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/uplink":
            self.send_response(404)
            self.end_headers()
            return

        tamanho = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(tamanho)

        try:
            dados = json.loads(corpo)
            print(json.dumps(dados, indent=2))
            dispositivo = dados["end_device_ids"]["device_id"]
            estado = dados["uplink_message"]["decoded_payload"]
            hora = datetime.datetime.now().strftime("%H:%M:%S")
            linha = (f"[{hora}] {dispositivo}: porta={estado['porta']} "
                     f"trinco={estado['trinco']}")
            if estado.get("alerta"):
                linha += "  <-- ALERTA: abertura forçada!"
            print(linha)
            self.send_response(200)
        except Exception as erro:
            print("Mensagem inválida:", erro)
            self.send_response(400)

        self.end_headers()

    def log_message(self, *args):
        pass  # esconde o registo automático do servidor


print(f"Recetor à escuta na porta {PORTA} (caminho /uplink). Ctrl+C para parar.")
HTTPServer(("0.0.0.0", PORTA), Receptor).serve_forever()