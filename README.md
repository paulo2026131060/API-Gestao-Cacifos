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
├── .env            ← DATABASE_URL, TTN_API_KEY, MANAGER_API_KEY, TTN_WEBHOOK_SECRET e NGROK_*; não vai para o Git
├── database/       ← tudo o que é da base de dados
│   ├── connection.py ← ligação à Neon (SQLAlchemy); escolhe TCP ou WebSocket
│   ├── neon_websocket.py ← proxy local: PostgreSQL dentro de wss:// (porta 443)
│   ├── schema.sql    ← tabelas lockers, students, requests e events
│   └── teste_db.py   ← teste rápido da ligação
├── static/         ← formulário do aluno (servido pela API em /)
│   ├── index.html
│   └── logo-unips.svg
├── main.py         ← endpoints, chave de API, histórico (log_event) + process_uplink
├── mqtt_ttn.py     ← cliente MQTT do TTN
├── tunel_webhook.py ← túnel ngrok + webhook do TTN (redes sem MQTT)
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

### Estados do pedido

```
Pending ──► Approved ──► Deposited ──► Collected
    │
    └──────► Rejected
```

| Transição | Quem | Regra |
|---|---|---|
| — → Pending | Website (aluno) | O pedido é sempre criado como Pending |
| Pending → Approved / Rejected | Gestor (app) | Só a partir de Pending |
| Approved → Deposited | Gestor (app) ou sensores | Tem de ter cacifo atribuído |
| Deposited → Collected | Gestor (app) ou sensores | Liberta o cacifo; o `locker_id` fica no pedido como histórico |

- Só pedidos **Approved** recebem, trocam ou perdem cacifo.
- **Rejected** e **Collected** são estados finais.
- A atribuição de cacifo é **atómica** (`UPDATE ... WHERE occupied = false`): dois gestores nunca ficam com o mesmo cacifo.

### Endpoints

| Método | Caminho | Função |
|---|---|---|
| GET | `/api/dashboard` | Contadores: pendentes, aprovados, aprovados sem cacifo, depositados, cacifos livres, ocupados e com alerta |
| GET | `/api/requests?status=&search=` | Lista de pedidos (mais antigos primeiro), com filtro por estado e pesquisa por aluno ou nº |
| POST | `/api/requests` | Cria um pedido (website). Corpo: `{"student": "...", "student_number": "2026100001", "material": "..."}` |
| GET | `/api/requests/{id}` | Detalhe do pedido, com o estado do cacifo atribuído |
| GET | `/api/requests/{id}/events` | Histórico do pedido (mais antigo primeiro) |
| PUT | `/api/requests/{id}/approve` · `/reject` | Pending → Approved / Rejected |
| PUT | `/api/requests/{id}/deposited` | Approved (com cacifo) → Deposited |
| PUT | `/api/requests/{id}/collected` | Deposited → Collected (liberta o cacifo) |
| PUT | `/api/requests/{id}/locker/{locker_id}` | Atribui ou troca o cacifo de um pedido Approved |
| PUT | `/api/requests/{id}/unassign` | Retira o cacifo de um pedido Approved |
| GET | `/api/lockers?status=free\|occupied\|alert` | Todos os cacifos, com estado do hardware e pedido/aluno ativo |
| GET | `/api/lockers/free` | Cacifos livres (diálogo "Atribuir cacifo") |
| GET | `/api/lockers/{id}` | Detalhe do cacifo, com os últimos 10 pedidos |
| GET | `/api/lockers/{id}/events?limit=50` | Histórico do cacifo: ações do gestor e eventos do sensor (mais recente primeiro) |
| POST | `/api/lockers/{id}/unlock` · `/lock` | Destranca / tranca o cacifo por downlink no ChirpStack (entregue após o próximo uplink) |
| POST | `/uplink` | Recebe um evento do cacifo (modo Wi-Fi ou testes) |

Respostas de erro:

| Código | Significado |
|---|---|
| 401 | Falta o cabeçalho `X-API-Key` ou a chave está errada (endpoints do gestor) |
| 404 | Pedido ou cacifo não existe |
| 409 | Ação impossível no estado atual (ex.: aprovar um pedido já Rejected, cacifo já ocupado) |
| 422 | Dados inválidos (ex.: estado desconhecido no filtro, campos em falta no POST, nome com números, número de aluno sem 10 dígitos) |
| 503 | `MANAGER_API_KEY` não está configurada no `.env` do servidor |

Documentação automática e testes: `http://localhost:8000/docs` (botão **Try it out**).

### Autenticação do gestor (chave de API)

Todos os endpoints `/api/...` exigem o cabeçalho `X-API-Key`, **exceto** `POST /api/requests`
(formulário do aluno). O formulário (`/`) e o `/uplink` também ficam abertos.

- A chave está no `.env`: `MANAGER_API_KEY=...` (gerar uma nova com
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
- Sem chave ou com chave errada → **401**; se o servidor não tiver a chave configurada → **503**.
- Em `/docs`, carregar em **Authorize** e colar a chave.
- Na app Android (Retrofit/OkHttp), juntar o cabeçalho a todos os pedidos com um interceptor:

  ```kotlin
  val client = OkHttpClient.Builder()
      .addInterceptor { chain ->
          chain.proceed(chain.request().newBuilder()
              .header("X-API-Key", BuildConfig.MANAGER_API_KEY)
              .build())
      }
      .build()
  ```

### Formulário do aluno

Com a API a correr, o formulário abre em **`http://localhost:8000/`** (ficheiro `static/index.html`,
servido pela própria API). Noutro dispositivo da mesma rede, usar `http://<IP-do-PC>:8000/`.

| Campo | Regra (validada no browser **e** na API) |
|---|---|
| Nome do aluno | Obrigatório, até 100 caracteres, **sem números** |
| Número do aluno | Obrigatório, **exatamente 10 dígitos** no formato `YYYYXXXXXX` (ex.: `2026100001`) |
| Material que pretende | Obrigatório, até 500 caracteres |

Ao enviar (`POST /api/requests`), a API, numa só transação:

1. regista o aluno em `students` pelo número (se já existir, atualiza o nome);
2. cria o pedido como `Pending`, ligado ao aluno pelo `student_number`;
3. grava o evento `request_created` no histórico.

O aluno vê "Pedido enviado! Um gestor entrará em contacto brevemente.". A página usa o logo e as
cores da UNIPS (`#009697`) e a fonte Barlow.

### Histórico (tabela `events`)

Cada ação fica registada automaticamente, na mesma transação da ação:

| Evento | Quando | `source` |
|---|---|---|
| `request_created` | Aluno envia o formulário | `student_form` |
| `status_changed` | Aprovar, rejeitar, depositar, levantar (guarda `old_status` → `new_status`) | `manager` |
| `locker_assigned` / `locker_unassigned` | Atribuir, trocar ou retirar o cacifo | `manager` |
| `unlock_requested` / `lock_requested` | Gestor pede para destrancar / trancar (downlink em fila) | `manager` |
| `door_opened` / `door_closed` | A porta muda de estado (uplinks repetidos não geram eventos) | `sensor` |
| `alert` | Começa um alerta de abertura forçada | `sensor` |

Consultar: `GET /api/requests/{id}/events` e `GET /api/lockers/{id}/events`, ou no Neon
`SELECT * FROM events ORDER BY id DESC;`. Os pedidos criados antes do histórico não têm eventos.

### Integração MQTT com o TTN (publish/subscribe)

O TTN tem um broker MQTT próprio. A API liga-se como **subscriber**:

- servidor `eu1.cloud.thethings.network`, porta **8883** (TLS);
- utilizador `projeto-redes-de-sensores@ttn`, password = API key gerada em
  **Integrations → MQTT** no TTN;
- tópico `v3/projeto-redes-de-sensores@ttn/devices/+/up`.

Fluxo de uma mensagem:

```
TTN publica → paho-mqtt → ao_receber() [mqtt_ttn.py] → process_uplink() [main.py] → UPDATE na Neon
```

O endpoint `/uplink` usa a mesma função `process_uplink`, por isso Wi-Fi e LoRaWAN atualizam a
base de dados da mesma forma. O cacifo é identificado pelo `dev_eui`.

Se a rede bloquear a porta 8883, a API mostra
`[MQTT] Não foi possível ligar ao TTN (...)` e continua a tentar em segundo plano.

### Integração MQTT com o ChirpStack do IPS

O IPS tem um servidor LoRaWAN próprio (ChirpStack). A API liga-se ao broker MQTT dele **ao mesmo
tempo** que ao TTN (`mqtt_chirpstack.py`), por isso os dois funcionam em paralelo. Os uplinks do
ChirpStack (v4 ou v3) são convertidos para o formato do TTN e passam pelo mesmo `process_uplink`.

Configuração no `.env` (sem `CHIRPSTACK_MQTT_HOST` a integração fica desligada):

```
CHIRPSTACK_MQTT_HOST=...          # endereço do broker MQTT do ChirpStack do IPS
CHIRPSTACK_MQTT_PORT=1883         # 8883 se o broker usar TLS
CHIRPSTACK_MQTT_TLS=false
CHIRPSTACK_MQTT_USER=...          # se o broker pedir autenticação
CHIRPSTACK_MQTT_PASSWORD=...
CHIRPSTACK_MQTT_TOPIC=application/<ID-da-application>/device/+/event/up
```

No ChirpStack:

- Criar o **device profile** (região EU868, versão LoRaWAN e ABP/OTAA iguais ao firmware) e, em
  **Codec**, escolher *JavaScript functions* e colar o mesmo `decodeUplink` da secção 4.
  Sem codec, os uplinks chegam sem `object` e a API ignora-os.
- Criar a **application** e o **device** com o DevEUI do XIAO; o cacifo é encontrado pelo
  `lockers.dev_eui` (a API converte o DevEUI para maiúsculas).

No arranque deve aparecer `[ChirpStack] Ligado a ... A subscrever ...` e, a cada uplink,
`[ChirpStack] Uplink em application/.../event/up`.

#### Trancar / destrancar (downlink)

`POST /api/lockers/{id}/unlock` e `/lock` (com `X-API-Key`) publicam no tópico
`application/<ID>/device/<deveui>/command/down` um downlink no FPort 1 (`01` = destrancar,
`00` = trancar; `data` em base64: `AQ==` / `AA==`). O ID da application vem do
`CHIRPSTACK_MQTT_TOPIC` (ou de `CHIRPSTACK_APPLICATION_ID`).

- O XIAO é **classe A**: o comando fica na fila do ChirpStack e só é entregue **logo a seguir ao
  próximo uplink** do cacifo. O novo estado do trinco chega nesse uplink (`locked` em
  `GET /api/lockers/{id}`).
- Cada pedido fica no histórico (`unlock_requested` / `lock_requested`).
- Respostas: **404** cacifo inexistente, **409** cacifo sem `dev_eui`, **503** sem ligação ao
  broker do ChirpStack (integração desligada ou rede a bloquear a 1883).
- O broker pode aceitar a publicação sem ter permissão para ela (o MQTT não avisa): confirmar na
  consola, no device → **Queue**, se o downlink aparece.

### Redes que bloqueiam portas (ex.: rede da universidade)

Na rede da universidade só passa HTTPS (porta 443): o MQTT (8883/1883), o PostgreSQL (5432)
e o túnel do Cloudflare (7844) estão bloqueados. A API contorna isto assim:

**Base de dados → WebSocket (automático).** Ao arrancar, `database/connection.py` testa a porta
5432. Se estiver bloqueada, mostra
`[DB] Porta 5432 indisponível: ligação à Neon por WebSocket (wss://, porta 443)` e liga-se à Neon
pelo proxy WebSocket da própria Neon (`wss://<host>/v2`, o mesmo do driver "serverless"). As
queries e as transações não mudam. Para forçar um modo: `DATABASE_TRANSPORT=tcp|websocket|auto`
no `.env`. Nesse modo o `channel_binding` da connection string é ignorado (o TLS é feito pelo
`wss://`).

**Uplinks do TTN → Webhook + ngrok.** Em vez de a API ir buscar os uplinks por MQTT, o TTN
envia-os por HTTPS para `POST /ttn/uplink`, através de um túnel ngrok (que só usa a porta 443).
O endpoint exige o cabeçalho `X-Webhook-Secret` igual a `TTN_WEBHOOK_SECRET` no `.env`
(sem ele → 401) e ignora mensagens sem `decoded_payload`.

Configurar uma vez:

1. Criar conta grátis em https://dashboard.ngrok.com e instalar: `winget install Ngrok.Ngrok`.
2. No `.env`:

   ```
   TTN_WEBHOOK_SECRET=...           # gerar com python -c "import secrets; print(secrets.token_urlsafe(32))"
   NGROK_AUTHTOKEN=...              # dashboard do ngrok → Your Authtoken
   NGROK_DOMAIN=xxxx.ngrok-free.app # dashboard do ngrok → Domains (domínio fixo gratuito)
   ```

3. No TTN: **Integrations → Webhooks → Add webhook → Custom webhook**
   - Webhook ID `api-cacifos`, formato **JSON**, Base URL `https://<NGROK_DOMAIN>`
   - Additional headers: `X-Webhook-Secret` = valor de `TTN_WEBHOOK_SECRET`
   - **Uplink message** ativado, caminho `/ttn/uplink`

   (Opcional: com `TTN_WEBHOOK_API_KEY` no `.env`, uma API key do TTN com permissão para editar as
   definições da application, o script cria/atualiza o webhook sozinho.)

Usar: arrancar a API e, noutro terminal, `python tunel_webhook.py`. Cada uplink aparece na API
como `[WEBHOOK] Uplink recebido do TTN`. Se o MQTT e o webhook estiverem ambos ativos (ex.: em
casa), cada uplink é processado duas vezes, sem problema: os eventos só são gravados quando o
estado muda.

---

## 6. Base de dados (Neon / PostgreSQL)

1. Na consola da Neon: **Connect** → copiar a connection string.
2. Guardar no ficheiro `.env`, **na pasta do projeto** (não dentro da `.venv`):

   ```
   DATABASE_URL=postgresql+psycopg://utilizador:password@ep-xxxx.neon.tech/neondb?sslmode=require
   ```

3. `database/connection.py` carrega o `.env` e cria o engine com `pool_pre_ping=True` e `pool_recycle=300`
   (a Neon fecha ligações paradas).
4. Criar as tabelas uma única vez com o conteúdo de `database/schema.sql` no **SQL Editor** da Neon.
5. Associar o cacifo ao dispositivo:

   ```sql
   UPDATE lockers SET dev_eui = '<DevEUI do dispositivo>' WHERE id = 1;
   ```

Drivers: `psycopg[binary]` para `postgresql+psycopg://` (ou `psycopg2-binary` para `postgresql://`).

### Tabelas

- **lockers**: `id`, `occupied` (atribuído a um pedido), `door_open`, `locked`,
  `has_item` (sensor físico), `alert`, `dev_eui`, `last_communication`.
- **requests**: `id`, `student`, `student_number` (→ `students.student_number`), `material`, `status`,
  `locker_id`, `created_at`.
- **students**: `id`, `student_number` (único, 10 dígitos `YYYYXXXXXX`, ex.: `2026100001`), `name`, `nfc_uid`. O formulário regista o aluno
  automaticamente pelo número; se o número já existir, reutiliza o aluno e atualiza o nome.
- **events** (histórico, gravado pela API): `id`, `event_date`, `event_type` (`request_created`,
  `status_changed`, `locker_assigned`, `locker_unassigned`, `door_opened`, `door_closed`, `alert`),
  `source` (`student_form`, `manager`, `sensor`), `request_id`, `locker_id`, `old_status`, `new_status`,
  `description`. Os eventos do sensor só são gravados quando a porta muda ou o alerta começa.
  Apagar um pedido apaga os eventos dele.

Relações e regras na base de dados:

```
students.student_number ◄── requests.student_number   (ON UPDATE CASCADE)
lockers.id              ◄── requests.locker_id
requests.id             ◄── events.request_id          (ON DELETE CASCADE)
lockers.id              ◄── events.locker_id           (ON DELETE SET NULL)
```

- O pedido **ativo** de um cacifo é o que tem esse `locker_id` e está `Approved` ou `Deposited`
  (a tabela `lockers` não guarda o pedido; a API faz o `JOIN` em `GET /api/lockers`).
- `students.student_number` tem um `CHECK` de 10 dígitos; um aluno com pedidos não pode ser apagado
  sem apagar primeiro os pedidos.
- `requests.status` e os booleanos de `lockers` são `NOT NULL`.
- Ao inserir linhas à mão **com id explícito**, acertar a sequência a seguir:
  `SELECT setval(pg_get_serial_sequence('requests','id'), (SELECT MAX(id) FROM requests));`

---

## 7. Testes sem o XIAO

1. **Ligação à base de dados:** `python -m database.teste_db` → `Ligado à base de dados!` + contagens.
2. **API → Neon:** com a API a correr, noutro terminal:

   ```powershell
   Invoke-RestMethod -Uri http://localhost:8000/uplink -Method Post -ContentType "application/json" -Body '{"end_device_ids":{"device_id":"cacifo-01-abp","dev_eui":"<DevEUI>"},"uplink_message":{"decoded_payload":{"porta":"ABERTA","trinco":"DESTRANCADO","alerta":false,"ocupado":false}}}'
   ```

   Confirmar na Neon: `SELECT id, door_open, locked, last_communication FROM lockers WHERE id = 1;`
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
| MQTT não liga na universidade | Rede bloqueia portas 8883/1883 | Webhook do TTN + `tunel_webhook.py` (ngrok, porta 443) |
| API pendurada na universidade (pedidos sem resposta) | Rede bloqueia a porta 5432 da Neon | Ligação automática por WebSocket (`database/neon_websocket.py`) |
| `cloudflared` não liga (`Allow outbound TCP on port 7844`) | Rede bloqueia a porta 7844 | Usar ngrok, que só precisa da porta 443 |
| `channel binding required but not supported` | `channel_binding=require` sem TLS direto no PostgreSQL | O `connection.py` retira-o no modo WebSocket |
| `KeyError: 'DATABASE_URL'` | `.env` dentro da `.venv` | Mover o `.env` para a pasta do projeto |
| `No module named 'psycopg'` | Connection string `postgresql+psycopg://` | `pip install "psycopg[binary]"` |
| `duplicate key value violates unique constraint "pedidos_pkey"` ao criar um pedido | Linhas inseridas à mão com id explícito; a sequência do `id` ficou para trás | `setval(...)` para o maior id (ver secção 6) |
| Endpoints novos dão 404 depois de alterar o `main.py` | O `--reload` do uvicorn nem sempre deteta a alteração (caminho com acentos) | Parar com `Ctrl+C` e arrancar de novo |

---

## 9. Próximos passos

- [x] `GET /api/lockers` devolver todos os cacifos com estado do hardware (para a app Android).
- [x] Estados finais do pedido e endpoints `POST /api/requests`, `/deposited`, `/collected`.
- [ ] App Android: Home, lista e detalhe de pedidos e cacifos.
- [x] Website com o formulário do aluno (usa `POST /api/requests`), com logo e cores da UNIPS.
- [x] Base de dados e API em inglês (tabelas, colunas, estados e caminhos).
- [x] Chave de API (`X-API-Key`) nos endpoints do gestor.
- [x] Histórico de pedidos e cacifos (tabela `events` + `GET .../events`).
- [x] Registo de alunos pelo formulário; número de aluno com 10 dígitos (`YYYYXXXXXX`).
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

Nunca publicar a connection string da Neon, a API key do TTN, a `MANAGER_API_KEY` nem as chaves
LoRaWAN. A `MANAGER_API_KEY` vai em texto simples enquanto a API não tiver HTTPS: serve para a rede
local; ao publicar a API na internet, usar HTTPS. Se a chave vazar, gerar outra, atualizar o `.env`,
reiniciar a API e atualizar a app.
As chaves partilhadas durante o desenvolvimento devem ser regeneradas no TTN no fim do projeto.
