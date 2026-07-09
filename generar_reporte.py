#!/usr/bin/env python3
"""
generar_reporte.py
=================
Dashboard diario de mercados para Juan.

Uso:
    python generar_reporte.py

Genera un archivo HTML self-contained (reporte_diario.html) con:
  - Cripto (BTC, ETH, top 10) via CoinGecko API (gratis)
  - Commodities agro (soja, trigo, maíz) via Yahoo Finance
  - Petróleo (WTI, Brent) via Yahoo Finance
  - Índices globales (S&P 500, NASDAQ, Dow) via Yahoo Finance
  - Argentina: Merval, ADRs (YPF, GGAL, BMA, PAM, TS, etc.)
  - Noticias via RSS feeds (Reuters, Yahoo Finance, CoinDesk)
  - Detección automática de oportunidades: RSI<30, caídas >5%, cerca de mín 52 sem

Requisitos (instalar una sola vez desde CMD/PowerShell):
    pip install yfinance requests feedparser pandas

Autor: Claude para Juan Degiovanangelo
"""

import os
import sys
import json
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def ahora_es() -> str:
    """Fecha/hora actual en español, zona horaria Argentina."""
    n = dt.datetime.now(TZ_AR)
    return f"{DIAS[n.weekday()].capitalize()} {n.day} de {MESES[n.month - 1]} de {n.year} - {n:%H:%M} hs (ARG)"

# ---- Chequeo de dependencias ----
try:
    import requests
    import feedparser
    import yfinance as yf
    import pandas as pd
except ImportError as e:
    print(f"[ERROR] Falta una librería: {e}")
    print("Ejecutá: pip install yfinance requests feedparser pandas")
    sys.exit(1)


# ============================================================
# CONFIGURACIÓN
# ============================================================
OUTPUT_PATH = Path(__file__).parent / "reporte_diario.html"

CRYPTOS = [
    ("bitcoin", "BTC", "Bitcoin"),
    ("ethereum", "ETH", "Ethereum"),
    ("binancecoin", "BNB", "BNB"),
    ("solana", "SOL", "Solana"),
    ("ripple", "XRP", "XRP"),
    ("cardano", "ADA", "Cardano"),
]

COMMODITIES = [
    ("ZS=F", "Soja (CBOT)", "¢/bu"),
    ("ZW=F", "Trigo (CBOT)", "¢/bu"),
    ("ZC=F", "Maíz (CBOT)", "¢/bu"),
    ("CL=F", "Petróleo WTI", "USD/bbl"),
    ("BZ=F", "Petróleo Brent", "USD/bbl"),
    ("NG=F", "Gas natural", "USD/MMBtu"),
    ("GC=F", "Oro", "USD/oz"),
]

INDICES_GLOBALES = [
    ("^GSPC", "S&P 500"),
    ("^IXIC", "NASDAQ Composite"),
    ("^DJI", "Dow Jones"),
    ("^VIX", "VIX (miedo)"),
]

ARGENTINA_ADRS = [
    ("YPF", "YPF (Energía)"),
    ("GGAL", "Grupo Galicia (Banco)"),
    ("BMA", "Banco Macro"),
    ("PAM", "Pampa Energía"),
    ("TS", "Tenaris"),
    ("TEO", "Telecom Argentina"),
    ("CEPU", "Central Puerto"),
    ("EDN", "Edenor"),
    ("IRS", "IRSA"),
    ("SUPV", "Grupo Supervielle"),
    ("ARGT", "ETF Argentina (Global X)"),
]

RSS_FEEDS = [
    ("https://feeds.reuters.com/reuters/businessNews", "Reuters Business"),
    ("https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "WSJ Markets"),
    ("https://www.cointelegraph.com/rss", "CoinTelegraph"),
    ("https://www.infobae.com/economia/feed/", "Infobae Economía"),
]


# ============================================================
# INDICADORES TÉCNICOS
# ============================================================
def rsi(closes: pd.Series, period: int = 14) -> float:
    """Calcula el RSI actual dado un array de cierres."""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return float((100 - (100 / (1 + rs))).iloc[-1])


def analizar_ticker(symbol: str) -> dict:
    """
    Devuelve un dict con precio, cambio %, RSI, distancia a mínimos de 52 semanas.
    """
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="1y")
        if hist.empty:
            return {"error": "sin datos"}

        precio = float(hist["Close"].iloc[-1])
        precio_prev = float(hist["Close"].iloc[-2])
        cambio_pct = ((precio - precio_prev) / precio_prev) * 100

        max_52w = float(hist["High"].max())
        min_52w = float(hist["Low"].min())
        dist_max = ((precio - max_52w) / max_52w) * 100
        dist_min = ((precio - min_52w) / min_52w) * 100

        rsi_val = rsi(hist["Close"]) if len(hist) > 15 else None

        # Detección de oportunidad
        oportunidad = None
        if rsi_val and rsi_val < 30:
            oportunidad = f"RSI oversold ({rsi_val:.0f})"
        elif dist_min < 5:
            oportunidad = f"A {dist_min:.1f}% del mínimo 52sem"
        elif cambio_pct < -5:
            oportunidad = f"Caída fuerte hoy ({cambio_pct:.1f}%)"

        return {
            "precio": precio,
            "cambio_pct": cambio_pct,
            "max_52w": max_52w,
            "min_52w": min_52w,
            "dist_max_pct": dist_max,
            "dist_min_pct": dist_min,
            "rsi": rsi_val,
            "oportunidad": oportunidad,
        }
    except Exception as e:
        return {"error": str(e)}


def obtener_cripto(coingecko_id: str) -> dict:
    """Consulta CoinGecko para un ID de moneda."""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
        params = {"localization": "false", "tickers": "false",
                  "community_data": "false", "developer_data": "false"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        market = d["market_data"]
        return {
            "precio": market["current_price"]["usd"],
            "cambio_24h": market["price_change_percentage_24h"],
            "cambio_7d": market["price_change_percentage_7d"],
            "cambio_30d": market["price_change_percentage_30d"],
            "max_ath": market["ath"]["usd"],
            "ath_dist_pct": market["ath_change_percentage"]["usd"],
            "market_cap": market["market_cap"]["usd"],
        }
    except Exception as e:
        return {"error": str(e)}


def obtener_noticias(limite: int = 10) -> list:
    """Junta headlines de los RSS feeds configurados."""
    noticias = []
    for url, fuente in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                noticias.append({
                    "titulo": entry.title,
                    "link": entry.link,
                    "fuente": fuente,
                    "fecha": entry.get("published", ""),
                })
        except Exception as e:
            print(f"[WARN] Falló RSS {fuente}: {e}")
    return noticias[:limite]


# ============================================================
# GENERACIÓN DEL HTML
# ============================================================
def card_activo(nombre: str, ticker: str, data: dict) -> str:
    if "error" in data:
        return f'''
        <div class="card">
          <div class="ticker">{ticker}</div>
          <div class="name">{nombre}</div>
          <div class="price flat">Sin datos</div>
          <div class="tech">Error: {data["error"]}</div>
        </div>'''

    precio = data["precio"]
    cambio = data.get("cambio_pct") or data.get("cambio_24h", 0)
    clase = "up" if cambio > 0 else ("down" if cambio < 0 else "flat")
    flecha = "▲" if cambio > 0 else ("▼" if cambio < 0 else "•")

    tech_lines = ""
    if "rsi" in data and data["rsi"] is not None:
        tech_lines += f'<div class="tech-line"><span>RSI(14):</span><span>{data["rsi"]:.0f}</span></div>'
    if "dist_max_pct" in data:
        tech_lines += f'<div class="tech-line"><span>Vs máx 52sem:</span><span class="down">{data["dist_max_pct"]:.1f}%</span></div>'
    if "dist_min_pct" in data:
        tech_lines += f'<div class="tech-line"><span>Vs mín 52sem:</span><span class="up">+{data["dist_min_pct"]:.1f}%</span></div>'

    oportunidad = ""
    if data.get("oportunidad"):
        oportunidad = f'<div style="margin-top:8px;padding:6px;background:rgba(63,185,80,0.15);border-radius:4px;font-size:12px;color:#3fb950;">💡 {data["oportunidad"]}</div>'

    return f'''
    <div class="card">
      <div class="ticker">{ticker}</div>
      <div class="name">{nombre}</div>
      <div class="price">${precio:,.2f}</div>
      <div class="change {clase}">{flecha} {cambio:+.2f}%</div>
      <div class="tech">{tech_lines}</div>
      {oportunidad}
    </div>'''


def generar_html() -> str:
    hoy = ahora_es()

    # Recolectar datos
    print("[1/5] Descargando datos de cripto...")
    cripto_data = [(sym, name, obtener_cripto(cgid)) for cgid, sym, name in CRYPTOS]

    print("[2/5] Descargando commodities...")
    commod_data = [(sym, name, analizar_ticker(sym)) for sym, name, _ in COMMODITIES]

    print("[3/5] Descargando índices globales...")
    idx_data = [(sym, name, analizar_ticker(sym)) for sym, name in INDICES_GLOBALES]

    print("[4/5] Descargando ADRs argentinos...")
    adr_data = [(sym, name, analizar_ticker(sym)) for sym, name in ARGENTINA_ADRS]

    print("[5/5] Descargando noticias...")
    news = obtener_noticias()

    # Detectar oportunidades globales
    oportunidades = []
    for lista in [commod_data, idx_data, adr_data]:
        for sym, name, data in lista:
            if data.get("oportunidad"):
                oportunidades.append(f'<div class="alert-box opportunity"><div class="alert-title">💡 {name} ({sym})</div><div class="alert-body">{data["oportunidad"]}</div></div>')

    # Renderizar cards
    cards_cripto = "\n".join(card_activo(name, sym, data) for sym, name, data in cripto_data)
    cards_commod = "\n".join(card_activo(name, sym, data) for sym, name, data in commod_data)
    cards_idx = "\n".join(card_activo(name, sym, data) for sym, name, data in idx_data)
    cards_adr = "\n".join(card_activo(name, sym, data) for sym, name, data in adr_data)

    news_html = ""
    for n in news:
        news_html += f'''
        <div class="news-item">
          <div class="news-title"><a href="{n["link"]}" target="_blank">{n["titulo"]}</a></div>
          <div class="news-meta">{n["fuente"]} · {n["fecha"]}</div>
        </div>'''

    oport_html = "\n".join(oportunidades) if oportunidades else '<div class="disclaimer">No se detectaron alertas técnicas fuertes hoy.</div>'

    # HTML template (mismo estilo que reporte_diario.html)
    return f'''<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte de Mercados — {hoy}</title>
<style>
:root {{ --bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;--green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff; }}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--text);padding:20px;line-height:1.5;}}
.header{{background:linear-gradient(135deg,#1e3a5f,#0d1117);padding:24px;border-radius:12px;margin-bottom:20px;border:1px solid var(--border);}}
.header h1{{font-size:24px;}} .header .date{{color:var(--muted);font-size:14px;}}
.tabs{{display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--border);flex-wrap:wrap;}}
.tab{{padding:12px 18px;background:transparent;border:none;color:var(--muted);cursor:pointer;font-size:14px;border-bottom:2px solid transparent;}}
.tab.active{{color:var(--blue);border-bottom-color:var(--blue);}}
.tab-content{{display:none;}} .tab-content.active{{display:block;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-bottom:24px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;}}
.card .ticker{{font-size:12px;color:var(--muted);}} .card .name{{font-size:15px;font-weight:600;margin:4px 0 8px;}}
.card .price{{font-size:22px;font-weight:700;margin-bottom:4px;}} .card .change{{font-size:13px;font-weight:500;}}
.up{{color:var(--green);}} .down{{color:var(--red);}} .flat{{color:var(--muted);}}
.card .tech{{margin-top:12px;padding-top:12px;border-top:1px solid var(--border);font-size:12px;color:var(--muted);}}
.card .tech-line{{display:flex;justify-content:space-between;margin-bottom:3px;}}
h2{{font-size:18px;margin:24px 0 12px;color:var(--text);}}
.alert-box{{background:linear-gradient(135deg,rgba(247,147,26,0.15),rgba(247,147,26,0.05));border:1px solid #f7931a;border-radius:8px;padding:16px;margin-bottom:12px;}}
.alert-box.opportunity{{background:linear-gradient(135deg,rgba(63,185,80,0.15),rgba(63,185,80,0.05));border-color:var(--green);}}
.alert-title{{font-weight:700;margin-bottom:6px;font-size:14px;}} .alert-body{{font-size:13px;}}
.news-item{{padding:12px 0;border-bottom:1px solid var(--border);}}
.news-title{{font-weight:600;font-size:14px;margin-bottom:4px;}}
.news-title a{{color:var(--text);text-decoration:none;}} .news-title a:hover{{color:var(--blue);}}
.news-meta{{font-size:12px;color:var(--muted);}}
.disclaimer{{background:rgba(210,153,34,0.1);border-left:3px solid var(--yellow);padding:12px 16px;margin:20px 0;font-size:12px;color:var(--muted);}}
.footer{{margin-top:40px;padding:16px;text-align:center;color:var(--muted);font-size:12px;border-top:1px solid var(--border);}}
</style></head><body>

<div class="header">
  <h1>📊 Reporte de Mercados</h1>
  <div class="date">{hoy} · Se actualiza cada hora automáticamente</div>
</div>

<h2>🚨 Alertas y oportunidades detectadas</h2>
{oport_html}

<div class="tabs">
  <button class="tab active" onclick="showTab(event,'cripto')">🪙 Cripto</button>
  <button class="tab" onclick="showTab(event,'agro')">🌾 Commodities</button>
  <button class="tab" onclick="showTab(event,'idx')">📈 Índices Globales</button>
  <button class="tab" onclick="showTab(event,'ar')">🇦🇷 Argentina</button>
  <button class="tab" onclick="showTab(event,'news')">📰 Noticias</button>
</div>

<div id="cripto" class="tab-content active"><div class="grid">{cards_cripto}</div></div>
<div id="agro" class="tab-content"><div class="grid">{cards_commod}</div></div>
<div id="idx" class="tab-content"><div class="grid">{cards_idx}</div></div>
<div id="ar" class="tab-content"><div class="grid">{cards_adr}</div></div>
<div id="news" class="tab-content">{news_html}</div>

<div class="disclaimer">
  <strong>⚠️ Aviso:</strong> reporte informativo automatizado. No constituye asesoramiento financiero.
  Verificá siempre en tu plataforma (Quantfury, Cocos, Binance) antes de operar.
</div>

<div class="footer">Reporte generado {hoy} · Fuentes: CoinGecko, Yahoo Finance, RSS feeds</div>

<script>
function showTab(e,id){{
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  e.currentTarget.classList.add('active');
}}
</script>
</body></html>'''


if __name__ == "__main__":
    print(f"\n=== Generando reporte horario ===")
    print(f"Fecha: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Output: {OUTPUT_PATH}\n")

    html = generar_html()
    OUTPUT_PATH.write_text(html, encoding="utf-8")

    print(f"\n✅ Reporte listo: {OUTPUT_PATH}")
    print(f"   Abrí el archivo en tu navegador para verlo.\n")
