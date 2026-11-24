// Cacifo - LoRaWAN em modo ABP (sem join)
// XIAO ESP32S3 + Wio-SX1262 (com a ANTENA LoRa ligada)
// Biblioteca: apenas RadioLib 7.x (servo controlado diretamente, sem ESP32Servo)
//
// Ligações:
//   Sensor de porta: D0 <-> GND (fio solto)
//   Servo: castanho -> GND | vermelho -> 5V | laranja/amarelo -> D1
//   D8, D9 e D10 ficam livres: são do rádio

#include <RadioLib.h>

// Chaves ABP do TTN (devAddr, nwkSKey, appSKey): ver secrets.example.h
// Copiar para secrets.h e preencher (ficheiro ignorado pelo Git)
#include "secrets.h"

// Rádio: NSS, DIO1, RESET, BUSY
SX1262 radio = new Module(41, 39, 42, 40);
LoRaWANNode node(&radio, &EU868);

// Pinos e ângulos
const int PIN_PORTA = D0;
const int PIN_SERVO = D1;

const int PIN_TRIG = D2;
const int PIN_ECHO = D3;
const int DISTANCIA_LIMITE_CM = 15; // Objeto a menos de 15 cm = CACIFO OCUPADO

bool ocupado = false;

const int ANG_TRANCADO    = 0;
const int ANG_DESTRANCADO = 90;

// Tempos
const unsigned long DEBOUNCE_MS      = 50;
const unsigned long INTERVALO_MIN_MS = 20000;  // mínimo entre envios (limites do TTN)
//const unsigned long INTERVALO_MIN_MS = 70000;  // SF11: ~66 s entre envios (regra dos 1%)

const unsigned long HEARTBEAT_MS     = 900000;  // "estou vivo" (testes; depois 15 min)

bool trancado = true;
bool portaFechada;
bool ultimaLeitura;
unsigned long ultimaMudanca = 0;

bool botaoAnterior = HIGH;
unsigned long ultimoClique = 0;

bool envioPendente = true;
unsigned long ultimoEnvio = 0;

// ---------- Mensagens ----------
void escrever(const char* texto) {
  Serial.println(texto);
  Serial.flush();
}

// ---------- Servo por PWM direto (50 Hz) ----------
const int SERVO_FREQ = 50;
const int SERVO_BITS = 14;
const int SERVO_CANAL = 0;

uint32_t anguloParaDuty(int angulo) {
  uint32_t pulsoUs = 500 + (uint32_t)angulo * (2400 - 500) / 180;
  return pulsoUs * ((1UL << SERVO_BITS) - 1) / 20000;
}

void servoIniciar() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_SERVO, SERVO_FREQ, SERVO_BITS);
#else
  ledcSetup(SERVO_CANAL, SERVO_FREQ, SERVO_BITS);
  ledcAttachPin(PIN_SERVO, SERVO_CANAL);
#endif
}

void servoMover(int angulo) {
  uint32_t duty = anguloParaDuty(angulo);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_SERVO, duty);
  delay(600);
  ledcWrite(PIN_SERVO, 0);
#else
  ledcWrite(SERVO_CANAL, duty);
  delay(600);
  ledcWrite(SERVO_CANAL, 0);
#endif
}

// ---------- Sensor Ultrassónico (Ocupação) ----------
float lerDistanciaCM() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  long duracao = pulseIn(PIN_ECHO, HIGH, 30000); // timeout de 30ms (~5 metros)
  if (duracao == 0) return 999.0; // sem eco / fora de alcance

  return duracao * 0.0343 / 2.0; // converte tempo em cm
}

bool verificarOcupacao() {
  float dist = lerDistanciaCM();
  // Se detetar algo mais perto do que o fundo do cacifo, tem encomenda
  return (dist > 0 && dist < DISTANCIA_LIMITE_CM);
}

// ---------- Porta e trinco ----------
bool lerPorta() { return digitalRead(PIN_PORTA) == LOW; }

void mostrarEstado() {
  Serial.print("Porta: ");
  Serial.print(portaFechada ? "FECHADA" : "ABERTA");
  Serial.print(" | Trinco: ");
  Serial.println(trancado ? "TRANCADO" : "DESTRANCADO");
  if (!portaFechada && trancado) {
    Serial.println("ALERTA: porta aberta com o trinco fechado (abertura forçada?)");
  }
  Serial.flush();
  ocupado = verificarOcupacao();
  Serial.print(" | Ocupação: ");
  Serial.print(ocupado ? "COM MATERIAL" : "VAZIO");
  Serial.print(" (");
  Serial.print(lerDistanciaCM());
  Serial.println(" cm)");
}

void destrancar() {
  trancado = false;
  servoMover(ANG_DESTRANCADO);
  mostrarEstado();
  envioPendente = true;
}

void trancar() {
  if (!portaFechada) {
    escrever("> Não é possível trancar: a porta está aberta");
    return;
  }
  trancado = true;
  servoMover(ANG_TRANCADO);
  mostrarEstado();
  envioPendente = true;
}

// ---------- LoRaWAN (ABP) ----------
void iniciarLoRaWAN() {


  escrever("A iniciar o rádio...");
  int state = radio.begin();
  Serial.print("radio.begin() = ");
  Serial.println(state);
  if (state != RADIOLIB_ERR_NONE) {
    escrever("ERRO no rádio. Parado.");
    while (true) delay(1000);
  }

  // LoRaWAN 1.0.x: as duas primeiras chaves (só da versão 1.1) ficam a NULL
  node.beginABP(devAddr, NULL, NULL, nwkSKey, appSKey);

  state = node.activateABP();
  Serial.print("activateABP() = ");
  Serial.println(state);
  if (state == RADIOLIB_LORAWAN_NEW_SESSION || state == RADIOLIB_ERR_NONE) {
    escrever("Sessão ABP ativa: pronto a enviar (sem join).");
    node.setADR(false);                 // desliga o ajuste automático
    int dr = node.setDatarate(1);       // Casa (longe do gateway) SF11 (~659 ms no ar)
    //int dr = node.setDatarate(5);       // Universidade (perto do gateway) SF7 (~50 ms no ar)
    Serial.print("setDatarate = ");
    Serial.println(dr);                 // 0 = aceite
    Serial.print("DevAddr em uso: 0x");
    Serial.println(node.getDevAddr(), HEX);
  } else {
    escrever("ERRO a ativar ABP. Confirma as chaves. Parado.");
    while (true) delay(1000);
  }
}

// Payload de 1 byte: bit0 = porta fechada | bit1 = trancado | bit2 = alerta
// Downlink: 01 = destrancar | 00 = trancar

void enviarEstado() {
  uint8_t estado = 0;
  if (portaFechada) estado |= 0x01;
  if (trancado)     estado |= 0x02;
  if (!portaFechada && trancado) estado |= 0x04;

  ocupado = verificarOcupacao();
  if (ocupado)      estado |= 0x08; // Bit 3 = Ocupado

  Serial.print("A enviar para o TTN: 0x0");
  Serial.println(estado, HEX);
  Serial.flush();

  uint8_t recebido[32];
  size_t tamanhoRecebido = 0;
  int state = node.sendReceive(&estado, 1, 1, recebido, &tamanhoRecebido);

  if (state < RADIOLIB_ERR_NONE) {
    Serial.print("Erro no envio: ");
    Serial.println(state);
    ultimoEnvio = millis();
    envioPendente = true;   // volta a tentar mais tarde
    return;
  } else {
    escrever("Enviado.");
    Serial.print("Tempo no ar: ");
    Serial.print(node.getLastToA());
    Serial.println(" ms");
    if (state > 0 && tamanhoRecebido > 0) {
      Serial.print("Downlink recebido: ");
      for (size_t i = 0; i < tamanhoRecebido; i++) {
        Serial.print(recebido[i], HEX);
        Serial.print(" ");
      }
      Serial.println();
      if (recebido[0] == 0x01) destrancar();
      else if (recebido[0] == 0x00) trancar();
    }
  }
  Serial.flush();
  ultimoEnvio = millis();
  envioPendente = false;
}



// ---------- Programa ----------
void setup() {
  Serial.begin(115200);
  unsigned long inicio = millis();
  while (!Serial && millis() - inicio < 10000) delay(10);
  delay(500);
  escrever("=== Cacifo LoRaWAN (ABP) ===");

  pinMode(PIN_PORTA, INPUT_PULLUP);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  portaFechada = lerPorta();
  ultimaLeitura = portaFechada;

  servoIniciar();
  servoMover(ANG_TRANCADO);

  iniciarLoRaWAN();

  escrever("Comandos: a = abrir trinco | f = fechar trinco | e = estado");
  mostrarEstado();
}

void loop() {
  // 1. Comandos
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'a') destrancar();
    else if (c == 'f') trancar();
    else if (c == 'e') mostrarEstado();
  }

  // 2. Sensor de porta (com debounce)
  bool leitura = lerPorta();
  if (leitura != ultimaLeitura) {
    ultimaMudanca = millis();
    ultimaLeitura = leitura;
  }
  if (millis() - ultimaMudanca > DEBOUNCE_MS && leitura != portaFechada) {
    portaFechada = leitura;
    mostrarEstado();
    envioPendente = true;
  }




  // 3. Envio: quando algo mudou (com intervalo mínimo) ou no heartbeat
  unsigned long desdeUltimo = millis() - ultimoEnvio;
  if ((envioPendente && desdeUltimo > INTERVALO_MIN_MS) || desdeUltimo > HEARTBEAT_MS) {
    enviarEstado();
  }
}
