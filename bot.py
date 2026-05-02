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

# CONFIG
BASE_URL = "https://app.previsao.io/api/v1"

MARKET_ID = 52605
SELECTION_SOBE = 104671
SELECTION_DESCE = 104672

CONFIDENCE_THRESHOLD = 70
SLEEP_TIME = 10

logging.basicConfig(level=logging.INFO)

# ENV
API_KEY = os.getenv("PREVISAO_API_KEY")
API_SECRET = os.getenv("PREVISAO_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not API_KEY or not API_SECRET or not TELEGRAM_TOKEN:
    raise Exception("Configure as variáveis de ambiente")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

usuarios = set()

@dataclass
class Conta:
    banca: float = 100.0
    risco: float = 0.02

conta = Conta()

# AUTH
def gerar_headers():
    raw = f"{API_KEY}:{API_SECRET}"
    token = base64.b64encode(raw.encode()).decode()

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

# API
def get_orderbook():
    response = requests.get(
        f"{BASE_URL}/orderbook",
        headers=gerar_headers(),
        params={"marketId": MARKET_ID, "limit": 50},
        timeout=5
    )
    response.raise_for_status()
    return response.json()

def get_market():
    response = requests.get(
        f"{BASE_URL}/markets/{MARKET_ID}",
        headers=gerar_headers(),
        timeout=5
    )
    response.raise_for_status()
    return response.json()["data"]

# ANALYSIS
def calcular_pressao(orderbook):
    sobe = 0
    desce = 0

    for sel in orderbook.get("selections", []):
        total = 0

        for b in sel.get("bids", []):
            total += float(b["quantity"])

        for a in sel.get("asks", []):
            total += float(a["quantity"])

        if sel["selectionId"] == SELECTION_SOBE:
            sobe = total
        elif sel["selectionId"] == SELECTION_DESCE:
            desce = total

    total = sobe + desce
    return (sobe - desce) / total if total > 0 else 0

def detectar_sinal(orderbook, hist):
    pressao = calcular_pressao(orderbook)
    hist.append(pressao)

    if len(hist) >= 10:
        if max(hist) - min(hist) < 0.08:
            return None

    if pressao > 0.25:
        return ("UP", int(min(100, pressao * 200)))

    if pressao < -0.25:
        return ("DOWN", int(min(100, abs(pressao) * 200)))

    return None

# TIME
def mercado_ativo(market):
    agora = datetime.now().astimezone()
    opens = datetime.fromisoformat(market["opensAt"])
    closes = datetime.fromisoformat(market["closesAt"])
    return opens <= agora <= closes

# TELEGRAM
def enviar(chat_id, direcao, confianca):
    valor = round(conta.banca * conta.risco, 2)

    msg = (
        f"🚨 SINAL\n\n"
        f"Direção: {direcao}\n"
        f"Confiança: {confianca}%\n\n"
        f"Entrada: R${valor}"
    )

    bot.send_message(chat_id, msg)

# LOOP
def loop_global():
    hist = deque(maxlen=50)
    ultimo = None

    while True:
        try:
            market = get_market()

            if not mercado_ativo(market):
                time.sleep(5)
                continue

            orderbook = get_orderbook()
            sinal = detectar_sinal(orderbook, hist)

            if sinal:
                direcao, conf = sinal

                if conf >= CONFIDENCE_THRESHOLD and direcao != ultimo:
                    for u in usuarios:
                        enviar(u, direcao, conf)

                    ultimo = direcao

            time.sleep(SLEEP_TIME)

        except Exception as e:
            print("Erro:", e)
            time.sleep(5)

# TELEGRAM HANDLER
@bot.message_handler(commands=['start'])
def start(msg):
    chat_id = msg.chat.id
    usuarios.add(chat_id)
    bot.send_message(chat_id, "🚀 Bot ativado")

# START
if __name__ == "__main__":
    bot.remove_webhook()

    threading.Thread(target=loop_global, daemon=True).start()

    print("Bot rodando...")

    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print("Erro polling:", e)
            time.sleep(5)
