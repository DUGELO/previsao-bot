import os
import time
import base64
import logging
import requests
import telebot
from dataclasses import dataclass
from collections import deque
from datetime import datetime
import threading

# ================= CONFIG =================
BASE_URL = "https://app.previsao.io/api/v1"

MARKET_ID = 52605
SELECTION_SOBE = 104671
SELECTION_DESCE = 104672

CONFIDENCE_THRESHOLD = 65
SLEEP_TIME = 10
COOLDOWN = 30  # segundos entre sinais

logging.basicConfig(level=logging.INFO)

# ================= ENV =================
API_KEY = os.getenv("PREVISAO_API_KEY")
API_SECRET = os.getenv("PREVISAO_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not API_KEY or not API_SECRET or not TELEGRAM_TOKEN:
    raise Exception("Configure as variáveis de ambiente")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

usuarios = set()

# ================= STATE =================
@dataclass
class Estado:
    ultimo_sinal: str = None
    ultimo_envio: float = 0
    ultimo_log: float = 0
    ultimo_heartbeat: float = 0
    ultimo_imbalance: float = 0
    uptime: float = time.time()

estado = Estado()

# ================= AUTH =================
def gerar_headers():
    token = base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

# ================= API =================
def get_orderbook():
    r = requests.get(
        f"{BASE_URL}/orderbook",
        headers=gerar_headers(),
        params={"marketId": MARKET_ID, "limit": 50},
        timeout=5
    )
    r.raise_for_status()
    return r.json()

def get_market():
    logging.info("Chamando API market...")
    log_telegram("Chamando API market...")
    r = requests.get(
        f"{BASE_URL}/markets/{MARKET_ID}",
        headers=gerar_headers(),
        timeout=5
    )
    r.raise_for_status()
    return r.json()["data"]

# ================= SIGNAL ENGINE =================
def calcular_imbalance(orderbook):
    sobe = 0
    desce = 0

    for sel in orderbook.get("selections", []):
        total_bid = sum(float(b["quantity"]) for b in sel.get("bids", []))

        if sel["selectionId"] == SELECTION_SOBE:
            sobe = total_bid
        elif sel["selectionId"] == SELECTION_DESCE:
            desce = total_bid

    total = sobe + desce
    return (sobe - desce) / total if total > 0 else 0

def detectar_sinal(hist):
    if len(hist) < 5:
        return None

    ultimos = list(hist)[-5:]

    media = sum(ultimos) / len(ultimos)
    variacao = max(ultimos) - min(ultimos)

    # filtro de lateralização
    if variacao < 0.05:
        return None

    # persistência
    if all(x > 0.12 for x in ultimos):
        score = int(min(100, abs(media) * 200))
        return ("UP", score, "Imbalance persistente positivo")

    if all(x < -0.12 for x in ultimos):
        score = int(min(100, abs(media) * 200))
        return ("DOWN", score, "Imbalance persistente negativo")

    return None

# ================= TIME =================
def mercado_ativo(market):
    agora = datetime.now().astimezone()
    opens = datetime.fromisoformat(market["opensAt"])
    closes = datetime.fromisoformat(market["closesAt"])
    return opens <= agora <= closes

# ================= TELEGRAM =================
def enviar(chat_id, direcao, confianca, motivo):
    msg = (
        f"🚨 SINAL\n\n"
        f"Direção: {direcao}\n"
        f"Confiança: {confianca}%\n\n"
        f"Motivo: {motivo}"
    )
    bot.send_message(chat_id, msg)

# ================= LOG TELEGRAM =================
LOG_CHAT_ID = None

def log_telegram(msg):
    if LOG_CHAT_ID:
        try:
            bot.send_message(LOG_CHAT_ID, msg)
        except Exception as e:
            logging.error(f"Erro log telegram: {e}")

@bot.message_handler(commands=['logs'])
def ativar_logs(msg):
    global LOG_CHAT_ID
    LOG_CHAT_ID = msg.chat.id
    bot.send_message(msg.chat.id, "📡 Logs ativados")

# ================= STATUS =================
@bot.message_handler(commands=['status'])
def status(msg):
    uptime = int(time.time() - estado.uptime)

    bot.send_message(msg.chat.id, f"""
📊 STATUS BOT

⏱ Uptime: {uptime}s
📈 Último imbalance: {estado.ultimo_imbalance:.4f}
📡 Último sinal: {estado.ultimo_sinal}
""")

# ================= START =================
@bot.message_handler(commands=['start'])
def start(msg):
    usuarios.add(msg.chat.id)
    bot.send_message(msg.chat.id, "🚀 Bot 2.0 ativado")

# ================= LOOP =================
def loop_global():
    hist = deque(maxlen=20)

    while True:
        try:
            market = get_market()

            # TEMP DEBUG
            # if not mercado_ativo(market):
            #     time.sleep(5)
            #     continue

            orderbook = get_orderbook()
            imbalance = calcular_imbalance(orderbook)

            hist.append(imbalance)
            estado.ultimo_imbalance = imbalance

            # 🔥 HEARTBEAT (LOG CONTROLADO)
            if time.time() - estado.ultimo_heartbeat > 30:
                msg = f"📊 Imbalance: {imbalance:.4f}"
                logging.info(msg)
                log_telegram(msg)
                estado.ultimo_heartbeat = time.time()

            sinal = detectar_sinal(hist)

            if sinal:
                direcao, conf, motivo = sinal

                agora = time.time()

                if (
                    conf >= CONFIDENCE_THRESHOLD
                    and direcao != estado.ultimo_sinal
                    and (agora - estado.ultimo_envio) > COOLDOWN
                ):
                    for u in usuarios:
                        enviar(u, direcao, conf, motivo)

                    estado.ultimo_sinal = direcao
                    estado.ultimo_envio = agora

                    log_telegram(f"🚨 SINAL {direcao} | {conf}%")
                    logging.info(f"SINAL {direcao} | {conf}%")

            time.sleep(SLEEP_TIME)

        except Exception as e:
            erro = f"❌ Erro loop: {e}"
            logging.error(erro)
            log_telegram(erro)
            time.sleep(5)

# ================= START APP =================
if __name__ == "__main__":
    bot.remove_webhook()

    threading.Thread(target=loop_global, daemon=True).start()

    logging.info("Bot rodando...")
    log_telegram("🚀 Bot iniciado")

    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            erro = f"❌ Erro polling: {e}"
            logging.error(erro)
            log_telegram(erro)
            time.sleep(5)