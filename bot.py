import os
import time
import base64
import logging
import requests
import telebot
from dataclasses import dataclass
from collections import deque
from datetime import datetime

# ================= CONFIG =================
BASE_URL = "https://app.previsao.io/api/v1"

MARKET_ID = 52605
SELECTION_SOBE = 104671
SELECTION_DESCE = 104672

CONFIDENCE_THRESHOLD = 70
SLEEP_TIME = 10

logging.basicConfig(level=logging.INFO)

# ================= ENV =================
API_KEY = os.getenv("PREVISAO_API_KEY")
API_SECRET = os.getenv("PREVISAO_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not all([API_KEY, API_SECRET, TELEGRAM_TOKEN]):
    raise Exception("Configure as variáveis de ambiente!")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ================= STATE =================
@dataclass
class Conta:
    banca: float = 100.0
    risco: float = 0.02
    loss_streak: int = 0
    max_loss_streak: int = 3

conta = Conta()

# ================= AUTH =================
def gerar_headers():
    raw = f"{API_KEY}:{API_SECRET}"
    token = base64.b64encode(raw.encode()).decode()

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

# ================= API =================
def get_orderbook():
    url = f"{BASE_URL}/orderbook"

    params = {
        "marketId": MARKET_ID,
        "limit": 50
    }

    response = requests.get(
        url,
        headers=gerar_headers(),
        params=params,
        timeout=5
    )

    response.raise_for_status()
    return response.json()

def get_market():
    url = f"{BASE_URL}/markets/{MARKET_ID}"

    response = requests.get(url, headers=gerar_headers(), timeout=5)
    response.raise_for_status()

    return response.json()["data"]

# ================= ANALYSIS =================
def calcular_pressao(orderbook):
    volume_sobe = 0
    volume_desce = 0

    for selection in orderbook.get("selections", []):
        selection_id = selection["selectionId"]

        bids = selection.get("bids", [])
        asks = selection.get("asks", [])

        volume_total = 0

        for b in bids:
            volume_total += float(b["quantity"])

        for a in asks:
            volume_total += float(a["quantity"])

        if selection_id == SELECTION_SOBE:
            volume_sobe = volume_total
        elif selection_id == SELECTION_DESCE:
            volume_desce = volume_total

    total = volume_sobe + volume_desce
    if total == 0:
        return 0

    return (volume_sobe - volume_desce) / total

def detectar_lateralizacao(historico):
    if len(historico) < 10:
        return False

    ultimos = list(historico)[-10:]
    return (max(ultimos) - min(ultimos)) < 0.08

def detectar_sinal(orderbook, historico):
    pressao = calcular_pressao(orderbook)
    historico.append(pressao)

    if detectar_lateralizacao(historico):
        return None

    if pressao > 0.25:
        return {
            "direcao": "UP",
            "confianca": int(min(100, pressao * 200)),
            "motivo": "Pressão dominante em SOBE"
        }

    elif pressao < -0.25:
        return {
            "direcao": "DOWN",
            "confianca": int(min(100, abs(pressao) * 200)),
            "motivo": "Pressão dominante em DESCE"
        }

    return None

# ================= TIME =================
def mercado_ativo(market):
    agora = datetime.now().astimezone()
    opens = datetime.fromisoformat(market["opensAt"])
    closes = datetime.fromisoformat(market["closesAt"])
    return opens <= agora <= closes

# ================= RISK =================
def valor_entrada():
    return round(conta.banca * conta.risco, 2)

def pode_operar():
    return conta.loss_streak < conta.max_loss_streak

# ================= TELEGRAM =================
def enviar_sinal(chat_id, sinal):
    valor = valor_entrada()

    msg = f"""
🚨 SINAL VALIDADO

📊 Direção: {sinal['direcao']}
🔥 Confiança: {sinal['confianca']}%

💰 Entrada: R${valor}

📈 Motivo:
{sinal['motivo']}

💼 Banca: R${conta.banca:.2f}
"""

    bot.send_message(chat_id, msg)

# ================= CORE =================
def run_bot(chat_id):
    historico = deque(maxlen=50)
    ultimo_sinal = None

    while True:
        try:
            if not pode_operar():
                bot.send_message(chat_id, "🛑 STOP ATIVADO")
                break

            market = get_market()

            if not mercado_ativo(market):
                time.sleep(5)
                continue

            orderbook = get_orderbook()
            sinal = detectar_sinal(orderbook, historico)

            if sinal and sinal["confianca"] >= CONFIDENCE_THRESHOLD:
                if ultimo_sinal != sinal["direcao"]:
                    enviar_sinal(chat_id, sinal)
                    ultimo_sinal = sinal["direcao"]

            time.sleep(SLEEP_TIME)

        except Exception as e:
            logging.error(e)
            time.sleep(5)

# ================= TELEGRAM HANDLER =================
@bot.message_handler(commands=['start'])
def start(msg):
    bot.send_message(msg.chat.id, "🚀 Bot rodando no Render")
    run_bot(msg.chat.id)

# ================= START =================
import threading

def start_bot():
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"Erro polling: {e}")
            time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=start_bot).start()
    print("🚀 Bot iniciado no Railway")

    while True:
        time.sleep(60)
