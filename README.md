# Smart Lockers – Gestão de Cacifos com LoRaWAN

Projeto de redes de sensores: cacifos inteligentes onde os alunos levantam encomendas/pedidos.
Cada cacifo tem um nó com sensores (porta, trinco e, mais tarde, ocupação e NFC) que comunica
por **LoRaWAN** com o **The Things Network (TTN)**. Uma **API em FastAPI** recebe os eventos
por **MQTT** e guarda o estado numa base de dados **PostgreSQL na Neon**. Um website (formulário
do aluno) e uma **app Android** (só para o gestor) usam essa API.

---

## 1. Arquitetura

```
[Sensor de porta + trinco]
          │
     XIAO ESP32S3 + Wio-SX1262
          │  LoRa 868 MHz (LoRaWAN, ABP/OTAA)
          ▼
   Gateway LoRaWAN do IPS (Kerlink, id-7076ff0056082d11)
          │
          ▼
   The Things Network (eu1) ──► payload formatter (byte → JSON)
          │  MQTT (broker do TTN, porta 8883)
          ▼
   API FastAPI (main.py + mqtt_ttn.py)
          │  SQLAlchemy
          ▼
   Base de dados PostgreSQL (Neon)
          ▲
   Website (formulário do aluno)   App Android (gestor)
```

Em desenvolvimento existe também um **modo Wi-Fi**: o XIAO envia o mesmo JSON diretamente para o
endpoint `/uplink` da API, sem passar pelo TTN.

---

## 2. Hardware

| Componente | Função |
|---|---|
| Seeed XIAO ESP32S3 | Microcontrolador do cacifo |
| Wio-SX1262 (conector B2B) | Rádio LoRa 868 MHz |
| Antena LoRa | Ligada ao conector da Wio-SX1262 |
| Micro servo SG90 | Trinco do cacifo (0° trancado, 90° destrancado) |
| Fio solto / botão | Simula o sensor de porta |
| HC-SR04 (futuro) | Sensor de ocupação (encomenda dentro do cacifo) |

### Ligações

| Ligação | Pino do XIAO |
|---|---|
| Sensor de porta (fio solto ou botão) | **D0** ↔ GND (ligado ao GND = porta fechada) |
| Servo – sinal (laranja) | **D1** |
| Servo – alimentação (vermelho) | **5V** |
| Servo – GND (castanho) | **GND** |
| HC-SR04 (futuro) | Trig **D2**, Echo **D3** (com divisor de tensão 1 kΩ / 2 kΩ) |

- O rádio usa o SPI **D8, D9, D10**; os restantes sinais (NSS 41, DIO1 39, RESET 42, BUSY 40)
  passam pelo conector plano. **D4 e D5** ficam reservados para o NFC.
- O botão deve ficar por cima da ranhura central da breadboard, com os fios em **pernas na diagonal**.
- As duas linhas laterais azuis (−) da breadboard **não estão ligadas entre si**: usar a que
  recebe o GND do XIAO.

---

## 3. Firmware do XIAO (Arduino IDE)

Configuração: placa **XIAO_ESP32S3**, **USB CDC On Boot: Enabled**, Serial Monitor a **115200**.
Biblioteca: **RadioLib 7.4.0** (a 8.x mudou a API e não compila com este código).
O servo é controlado por PWM direto (LEDC), sem a biblioteca ESP32Servo.

| Programa | Uso |
|---|---|
| `sensor_porta_sem_leds.ino` | Fase 2: porta + trinco, sem rádio |
| `teste_radio.ino` | Confirma que o rádio SX1262 responde (`radio.begin() = 0`) |
| `cacifo_wifi.ino` / `cacifo_wifi_v2.ino` | Modo Wi-Fi: envia JSON para `http://<IP>:8000/uplink` (v2 inclui ocupação) |
| `cacifo_lorawan_abp.ino` | **Versão principal**: LoRaWAN em ABP |

Comandos no Serial Monitor: `a` = abrir trinco, `f` = fechar trinco, `e` = estado.

### Payload (1 byte)

| Bit | Significado |
|---|---|
| 0 | Porta fechada |
| 1 | Trancado |
| 2 | Alerta (porta aberta com trinco trancado → abertura forçada) |
| 3 | Ocupado (sensor de ocupação, futuro) |

Downlink (FPort 1): `01` = destrancar, `00` = trancar.

### Parâmetros de rádio

| Local | Data rate | Intervalo mínimo entre envios |
|---|---|---|
| Casa (Setúbal, longe do gateway) | `setDatarate(1)` → SF11 (~659 ms no ar) | 70 s |
| Universidade (perto do gateway) | `setDatarate(5)` → SF7 (~50 ms no ar) | 10 s |

- `node.setADR(false)` antes do `setDatarate`, senão o data rate é ignorado.
- Heartbeat a cada 15 minutos (limite de ~30 s de tempo no ar por dia no TTN).
- Regra europeia de **1% de duty cycle** na banda 868 MHz.

---

## 4. The Things Network

Application: `projeto-redes-de-sensores` (cluster **eu1**).

| Dispositivo | Ativação | Notas |
|---|---|---|
| `cacifo-01` | OTAA, LoRaWAN 1.1.0 | Join só funciona perto da universidade |
| `cacifo-01-abp` | ABP, LoRaWAN 1.0.3 | Usado a partir de casa; **Resets frame counters** ativo |

- Frequency plan: **Europe 863-870 MHz (SF12 for RX2)**; classe A.
- As chaves (AppKey, NwkKey, NwkSKey, AppSKey) ficam **só no código do XIAO e no TTN**, nunca no GitHub.
- No `ns.up.data.forward` o payload aparece **encriptado**; o valor desencriptado está no evento
  "Forward uplink data message" (Application Server).

### Payload formatter (Uplink, ao nível da application)

```javascript
function decodeUplink(input) {
  var b = input.bytes[0];
  return {
    data: {
      porta:   (b & 0x01) ? "FECHADA" : "ABERTA",
      trinco:  (b & 0x02) ? "TRANCADO" : "DESTRANCADO",
      alerta:  (b & 0x04) ? true : false,
      ocupado: (b & 0x08) ? true : false
    }
  };
}
```

---

## 5. API (FastAPI)

### Estrutura

```
API-Gestão-Cacifos/
├── .venv/
├── .env            ← DATABASE_URL (e TTN_API_KEY); não vai para o Git
├── database.py     ← ligação à Neon (SQLAlchemy)
├── main.py         ← endpoints + processar_uplink
├── mqtt_ttn.py     ← cliente MQTT do TTN
├── schema.sql      ← tabelas cacifos e pedidos
├── teste_db.py     ← teste rápido da ligação à base de dados
└── requirements.txt
```

### Instalação e arranque

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Arrancar sempre com `python -m uvicorn` e confirmar no terminal a linha
`[MQTT] Ligado ao TTN. A subscrever os uplinks...`.

### Endpoints

| Método | Caminho | Função |
|---|---|---|
| GET | `/api/dashboard` | Contadores (pendentes, preparados, aprovados, cacifos livres/ocupados) |
| GET | `/api/pedidos?estado=` | Lista de pedidos, filtrada por estado |
| GET | `/api/pedidos/{id}` | Detalhe de um pedido |
| PUT | `/api/pedidos/{id}/aprovar` · `/rejeitar` · `/preparado` | Muda o estado do pedido |
| GET | `/api/cacifos` | Cacifos livres |
| PUT | `/api/pedidos/{id}/cacifo/{cacifo_id}` | Associa um cacifo ao pedido |
| PUT | `/api/pedidos/{id}/desassociar` | Liberta o cacifo do pedido |
| POST | `/uplink` | Recebe um evento do cacifo (modo Wi-Fi ou testes) |

Documentação automática: `http://localhost:8000/docs`.

### Integração MQTT com o TTN (publish/subscribe)

O TTN tem um broker MQTT próprio. A API liga-se como **subscriber**:

- servidor `eu1.cloud.thethings.network`, porta **8883** (TLS);
- utilizador `projeto-redes-de-sensores@ttn`, password = API key gerada em
  **Integrations → MQTT** no TTN;
- tópico `v3/projeto-redes-de-sensores@ttn/devices/+/up`.

Fluxo de uma mensagem:

```
TTN publica → paho-mqtt → ao_receber() [mqtt_ttn.py] → processar_uplink() [main.py] → UPDATE na Neon
```

O endpoint `/uplink` usa a mesma função `processar_uplink`, por isso Wi-Fi e LoRaWAN atualizam a
base de dados da mesma forma. O cacifo é identificado pelo `dev_eui`.

---

## 6. Base de dados (Neon / PostgreSQL)

1. Na consola da Neon: **Connect** → copiar a connection string.
2. Guardar no ficheiro `.env`, **na pasta do projeto** (não dentro da `.venv`):

   ```
   DATABASE_URL=postgresql+psycopg://utilizador:password@ep-xxxx.neon.tech/neondb?sslmode=require
   ```

3. `database.py` carrega o `.env` e cria o engine com `pool_pre_ping=True` e `pool_recycle=300`
   (a Neon fecha ligações paradas).
4. Criar as tabelas uma única vez com o conteúdo de `schema.sql` no **SQL Editor** da Neon.
5. Associar o cacifo ao dispositivo:

   ```sql
   UPDATE cacifos SET dev_eui = '<DevEUI do dispositivo>' WHERE id = 1;
   ```

Drivers: `psycopg[binary]` para `postgresql+psycopg://` (ou `psycopg2-binary` para `postgresql://`).

### Tabelas

- **cacifos**: `id`, `ocupado` (atribuído a um pedido), `porta_aberta`, `trancado`,
  `tem_encomenda` (sensor físico), `alerta`, `dev_eui`, `ultima_comunicacao`.
- **pedidos**: `id`, `aluno`, `material`, `estado`, `cacifo_id`, `criado_em`.

---

## 7. Testes sem o XIAO

1. **Ligação à base de dados:** `python teste_db.py` → `Ligado à base de dados!` + contagens.
2. **API → Neon:** com a API a correr, noutro terminal:

   ```powershell
   Invoke-RestMethod -Uri http://localhost:8000/uplink -Method Post -ContentType "application/json" -Body '{"end_device_ids":{"device_id":"cacifo-01-abp","dev_eui":"<DevEUI>"},"uplink_message":{"decoded_payload":{"porta":"ABERTA","trinco":"DESTRANCADO","alerta":false,"ocupado":false}}}'
   ```

   Confirmar na Neon: `SELECT id, porta_aberta, trancado, ultima_comunicacao FROM cacifos WHERE id = 1;`
3. **TTN → MQTT → API → Neon:** no TTN, `cacifo-01-abp` → **Messaging → Simulate uplink**,
   FPort `1`, payload `03` ou `00`.

---

## 8. Problemas encontrados e soluções

| Problema | Causa | Solução |
|---|---|---|
| `radio.begin()` não compila | RadioLib 8.x mudou a API | Usar RadioLib **7.4.0** |
| Serial Monitor vazio | Porta USB muda após upload / mensagens perdidas no arranque | Esperar pelo Serial no `setup()`, confirmar a porta, `USB CDC On Boot: Enabled` |
| "DevNonce is too small" (OTAA) | O DevNonce recomeça a cada reinício | Guardar o contador com `Preferences`; em testes, "Resets join nonces" |
| Join OTAA falha (-1116) a partir de casa | Join-accept do gateway não chega a Setúbal (RSSI ~-118 dBm) | Usar **ABP** em casa; OTAA perto da universidade |
| Uplinks ABP não chegavam ao TTN | Tabela de RF switch (GPIO38) enfraquecia a transmissão | Retirar a tabela do interruptor de antena |
| Mensagens perdidas / no limite | SNR entre -10 e -16 dB | SF11, antena **na vertical**, junto a uma janela |
| Mensagens em falta em SF11 | Duty cycle de 1% | Intervalo mínimo de 70 s e reenvio em caso de erro |
| XIAO não chega à API | Processo antigo a ouvir na porta 8000 / Firewall | `netstat -ano \| findstr :8000` e `taskkill`; regra de Firewall para a porta 8000 |
| MQTT não liga na universidade | Rede bloqueia portas 8883/1883 | Usar o hotspot do telemóvel (ou webhook + ngrok) |
| `KeyError: 'DATABASE_URL'` | `.env` dentro da `.venv` | Mover o `.env` para a pasta do projeto |
| `No module named 'psycopg'` | Connection string `postgresql+psycopg://` | `pip install "psycopg[binary]"` |

---

## 9. Próximos passos

- [ ] `GET /api/cacifos` devolver todos os cacifos com estado do hardware (para a app Android).
- [ ] App Android: Home, lista e detalhe de pedidos e cacifos.
- [ ] Downlink a partir da app (destrancar cacifo via TTN), testado perto da universidade.
- [ ] Regras automáticas: atualizar o estado do pedido (Depositado/Levantado) a partir dos eventos.
- [ ] Sensor de ocupação (HC-SR04) e leitor NFC (PN532).
- [ ] Voltar ao OTAA na versão final.

---

## 10. Segurança

`.gitignore` mínimo:

```
.env
.venv/
__pycache__/
```

Nunca publicar a connection string da Neon, a API key do TTN nem as chaves LoRaWAN.
As chaves partilhadas durante o desenvolvimento devem ser regeneradas no TTN no fim do projeto.
