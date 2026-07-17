#!/usr/bin/env python3
"""
generar_reporte.py
==================
Dashboard diario de mercados para Juan.

Genera un archivo HTML self-contained (reporte_diario.html) con:
  - Cripto (BTC, ETH, top monedas) via CoinGecko API (gratis)
  - GRANOS multi-mercado:
        * Chicago (CBOT)          -> Yahoo Finance + Bolsa de Cereales
        * Pizarra Rosario (BCR)   -> Cámara Arbitral de Cereales (cac.bcr.com.ar)
        * Bahía Blanca            -> Bolsa de Cereales (Cámara Arbitral)
        * MATBA Rofex (A3)        -> Bolsa de Cereales (Mercado de Futuros)
  - Energía y metales (WTI, Brent, gas, oro) via Yahoo Finance
  - Índices globales (S&P 500, NASDAQ, Dow, VIX) via Yahoo Finance
  - Argentina: ADRs (YPF, GGAL, BMA, PAM, TS, etc.)
  - Noticias: AGRO argentino + GOBIERNO / política / economía (RSS)
  - Detección automática de oportunidades: RSI<30, caídas >5%, cerca de mín 52 sem

------------------------------------------------------------------------------
CÓMO EDITAR (desde GitHub):
  - Todo lo configurable está en la sección "CONFIGURACIÓN" de abajo.
  - Para agregar/quitar un grano, mercado, ticker o feed de noticias,
    editá las listas correspondientes. No hace falta tocar el resto.
  - Las fuentes argentinas se scrapean de HTML. Si un sitio cambia su
    estructura, el script no rompe: la tarjeta muestra "s/c" (sin cotización)
    y se puede ajustar el parser puntual.
------------------------------------------------------------------------------

Requisitos:
    pip install yfinance requests feedparser pandas beautifulsoup4 lxml

Autor: Claude para Juan Degiovanangelo
"""

import sys
import re
import datetime as dt
from pathlib import Path

# ---- Chequeo de dependencias ----
try:
    import requests
    import feedparser
    import yfinance as yf
    import pandas as pd
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"[ERROR] Falta una librería: {e}")
    print("Ejecutá: pip install yfinance requests feedparser pandas beautifulsoup4 lxml")
    sys.exit(1)


# ============================================================
# CONFIGURACIÓN
# ============================================================
OUTPUT_PATH = Path(__file__).parent / "reporte_diario.html"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36"}
TIMEOUT = 25  # segundos por request

# ---- Fuentes de precios ARGENTINA (editá las URLs si cambian) ----
URL_ROSARIO = "https://www.cac.bcr.com.ar/es/precios-de-pizarra"
URL_CAMARA  = "https://www.bolsadecereales.com/camara-arbitral"   # incluye Bahía Blanca
URL_MERCADO = "https://www.bolsadecereales.com/mercados"          # futuros MATBA Rofex (A3)

# ---- Cripto (id CoinGecko, símbolo, nombre) ----
CRYPTOS = [
    ("bitcoin", "BTC", "Bitcoin"),
    ("ethereum", "ETH", "Ethereum"),
    ("binancecoin", "BNB", "BNB"),
    ("solana", "SOL", "Solana"),
    ("ripple", "XRP", "XRP"),
    ("cardano", "ADA", "Cardano"),
]

# ---- Granos Chicago (CBOT) para tarjetas técnicas (RSI, 52sem) via Yahoo ----
GRANOS_CBOT = [
    ("ZS=F", "Soja (CBOT)", "¢/bu"),
    ("ZC=F", "Maíz (CBOT)", "¢/bu"),
    ("ZW=F", "Trigo (CBOT)", "¢/bu"),
]

# ---- Energía y metales via Yahoo ----
ENERGIA_METALES = [
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

# ---- Noticias: AGRO argentino ----
NOTICIAS_AGRO = [
    ("https://bichosdecampo.com/feed/", "Bichos de Campo"),
    ("https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/campo/?outputType=xml", "La Nación Campo"),
    ("https://www.infobae.com/arc/outboundfeeds/rss/category/economia/campo/?outputType=xml", "Infobae Campo"),
    ("https://www.agrofy.com.ar/rss.xml", "Agrofy News"),
]

# ---- Noticias: GOBIERNO / política / economía ----
NOTICIAS_GOB = [
    ("https://www.infobae.com/arc/outboundfeeds/rss/category/politica/?outputType=xml", "Infobae Política"),
    ("https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/politica/?outputType=xml", "La Nación Política"),
    ("https://www.infobae.com/arc/outboundfeeds/rss/category/economia/?outputType=xml", "Infobae Economía"),
    ("https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/economia/?outputType=xml", "La Nación Economía"),
]

# Estado global (dólar BNA, fecha pizarra) que llenan los scrapers ARG
ESTADO = {"tc_bna": None, "fecha_rosario": None}


# ============================================================
# HELPERS
# ============================================================
def ar_num(s):
    """Convierte '285.800,00' -> 285800.00 ; '195,02' -> 195.02 ; '318.2' -> 318.2"""
    if s is None:
        return None
    s = str(s).strip().replace("$", "").replace("US", "").replace("(E)", "").strip()
    if s in ("", "s/c", "S/C", "-", "—"):
        return None
    # Formato argentino con miles '.' y decimal ','
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(re.sub(r"[^\d.\-]", "", s))
    except ValueError:
        return None


def fmt_usd(v, dec=2):
    return f"US$ {v:,.{dec}f}" if v is not None else "s/c"


def fmt_ars(v):
    return f"${v:,.0f}" if v is not None else "s/c"


# ============================================================
# INDICADORES TÉCNICOS (Yahoo)
# ============================================================
def rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return float((100 - (100 / (1 + rs))).iloc[-1])


def analizar_ticker(symbol):
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

        oportunidad = None
        if rsi_val and rsi_val < 30:
            oportunidad = f"RSI oversold ({rsi_val:.0f})"
        elif dist_min < 5:
            oportunidad = f"A {dist_min:.1f}% del mínimo 52sem"
        elif cambio_pct < -5:
            oportunidad = f"Caída fuerte hoy ({cambio_pct:.1f}%)"

        return {
            "precio": precio, "cambio_pct": cambio_pct,
            "max_52w": max_52w, "min_52w": min_52w,
            "dist_max_pct": dist_max, "dist_min_pct": dist_min,
            "rsi": rsi_val, "oportunidad": oportunidad,
        }
    except Exception as e:
        return {"error": str(e)}


def obtener_cripto(coingecko_id):
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
        params = {"localization": "false", "tickers": "false",
                  "community_data": "false", "developer_data": "false"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        market = r.json()["market_data"]
        return {
            "precio": market["current_price"]["usd"],
            "cambio_24h": market["price_change_percentage_24h"],
            "cambio_7d": market["price_change_percentage_7d"],
            "cambio_30d": market["price_change_percentage_30d"],
            "ath_dist_pct": market["ath_change_percentage"]["usd"],
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# PRECIOS ARGENTINA — SCRAPERS
# ============================================================
def precios_rosario():
    """
    Pizarra Rosario (Cámara Arbitral de Cereales - BCR).
    Devuelve {grano: {"ars": float, "usd": float}} en $/Tn y US$/Tn.
    Robusto a que los valores vengan inline o en líneas separadas
    (normaliza los espacios y busca por grano en una ventana acotada).
    """
    granos = [("Trigo", r"Trigo"), ("Maíz", r"Ma[íi]z"), ("Girasol", r"Girasol"),
              ("Soja", r"Soja"), ("Sorgo", r"Sorgo")]
    out = {}
    try:
        r = requests.get(URL_ROSARIO, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        txt = re.sub(r"[ \t]+", " ", soup.get_text("\n"))
        flat = re.sub(r"\s+", " ", txt)   # todo en una línea

        mf = re.search(r"Precios Pizarra del d[ií]a\s*([\d/]+)", flat)
        if mf:
            ESTADO["fecha_rosario"] = mf.group(1)
        mtc = re.search(r"Comprador[^:]*:\s*\$?\s*([\d\.]+,\d{2})", flat)
        if mtc:
            ESTADO["tc_bna"] = ar_num(mtc.group(1))

        # Anclar a partir del título de la pizarra para no matchear el menú
        cuerpo = flat[mf.end():] if mf else flat
        for nombre, pat in granos:
            # Ventana desde el nombre del grano (120 chars) para tomar SU $ y US$
            m = re.search(pat, cuerpo)
            if not m:
                continue
            ventana = cuerpo[m.end(): m.end() + 120]
            m_ars = re.search(r"\$\s*([\d\.]+,\d{2})", ventana)
            m_usd = re.search(r"US\$\s*\(?E?\)?\s*([\d\.]+,\d{2})", ventana)
            out[nombre] = {
                "ars": ar_num(m_ars.group(1)) if m_ars else None,
                "usd": ar_num(m_usd.group(1)) if m_usd else None,
            }
    except Exception as e:
        print(f"[WARN] Rosario falló: {e}")
    return out


def precios_bahia_blanca():
    """
    Bahía Blanca (Cámara Arbitral, vía Bolsa de Cereales).
    Devuelve {grano: {"usd": float}} en US$/Tn (toma 'Actual', si es s/c usa 'Anterior').
    Robusto: normaliza espacios dentro de la sección Bahía Blanca > Dólares/TN.
    """
    out = {}
    granos = [("Cebada Forrajera", r"Cebada Forrajera"), ("Trigo", r"Trigo"),
              ("Maíz", r"Ma[íi]z"), ("Soja", r"Soja"), ("Girasol", r"Girasol")]
    try:
        r = requests.get(URL_CAMARA, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        txt = soup.get_text("\n")

        # Acotar a la sección de Bahía Blanca (hasta el próximo puerto)
        mb = re.search(r"Bah[íi]a Blanca", txt)
        if not mb:
            return out
        resto = txt[mb.end():]
        mfin = re.search(r"C[óo]rdoba|Descargar Cotizaci[óo]n", resto)
        seccion = resto[: mfin.start()] if mfin else resto[:2000]

        # Preferir el sub-bloque en dólares
        md = re.search(r"D[óo]lares", seccion)
        sub = seccion[md.start():] if md else seccion
        flat = re.sub(r"\s+", " ", sub)

        for nombre, pat in granos:
            # Grano seguido de dos valores (Actual, Anterior); cada uno número o s/c
            m = re.search(pat + r"\s+(s/c|[\d\.,]+)\s+(s/c|[\d\.,]+)", flat, re.I)
            if not m:
                continue
            actual = None if m.group(1).lower() == "s/c" else ar_num(m.group(1))
            anterior = None if m.group(2).lower() == "s/c" else ar_num(m.group(2))
            val = actual if actual is not None else anterior
            if nombre not in out and val is not None:
                out[nombre] = {"usd": val}
    except Exception as e:
        print(f"[WARN] Bahía Blanca falló: {e}")
    return out


def precios_matba_rofex():
    """
    Futuros MATBA Rofex (A3) vía Bolsa de Cereales / Mercado de Futuros.
    Devuelve {"ROS": {grano:{"pos","precio"}}, "CHICAGO": {grano:{"pos","precio"}}}
    en US$/Tn, tomando la posición más cercana de cada grano.
    """
    out = {"ROS": {}, "CHICAGO": {}}
    mapa = {"SOJA": "Soja", "MAIZ": "Maíz", "TRIGO": "Trigo"}
    try:
        r = requests.get(URL_MERCADO, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        try:
            from io import StringIO
            tablas = pd.read_html(StringIO(r.text))
        except Exception:
            tablas = []

        filas = []
        for df in tablas:
            df = df.astype(str)
            for _, row in df.iterrows():
                celdas = [c for c in row.tolist() if c and c != "nan"]
                if celdas:
                    filas.append(celdas)

        if not filas:
            soup = BeautifulSoup(r.text, "lxml")
            for l in soup.get_text("\n").split("\n"):
                filas.append(l.split())

        for celdas in filas:
            joined = " ".join(celdas)
            m = re.match(r"(SOJA|MAIZ|TRIGO)\s+(ROS|CHICAGO)", joined)
            if not m:
                continue
            grano = mapa[m.group(1)]
            mercado = "ROS" if m.group(2) == "ROS" else "CHICAGO"
            if "MINI" in joined:
                continue
            pos = re.search(r"(\d{2}/\d{4})", joined)
            nums = re.findall(r"\d+\.\d+", joined)
            precio = None
            for n in nums:
                v = float(n)
                if 50 < v < 2000:      # rango razonable US$/t granos
                    precio = v
            if grano not in out[mercado] and precio:
                out[mercado][grano] = {
                    "pos": pos.group(1) if pos else "",
                    "precio": precio,
                }
    except Exception as e:
        print(f"[WARN] MATBA Rofex falló: {e}")
    return out


# ============================================================
# NOTICIAS
# ============================================================
def obtener_noticias(feeds, por_feed=3, limite=12):
    noticias = []
    for url, fuente in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:por_feed]:
                noticias.append({
                    "titulo": entry.get("title", "(sin título)"),
                    "link": entry.get("link", "#"),
                    "fuente": fuente,
                    "fecha": entry.get("published", entry.get("updated", "")),
                })
        except Exception as e:
            print(f"[WARN] RSS {fuente} falló: {e}")
    return noticias[:limite]


# ============================================================
# HTML — COMPONENTES
# ============================================================
def card_activo(nombre, ticker, data):
    if "error" in data:
        return (f'<div class="card"><div class="ticker">{ticker}</div>'
                f'<div class="name">{nombre}</div><div class="price flat">Sin datos</div>'
                f'<div class="tech">Error: {data["error"]}</div></div>')
    precio = data["precio"]
    cambio = data.get("cambio_pct") or data.get("cambio_24h", 0)
    clase = "up" if cambio > 0 else ("down" if cambio < 0 else "flat")
    flecha = "▲" if cambio > 0 else ("▼" if cambio < 0 else "•")
    tech = ""
    if data.get("rsi") is not None:
        tech += f'<div class="tech-line"><span>RSI(14):</span><span>{data["rsi"]:.0f}</span></div>'
    if "dist_max_pct" in data:
        tech += f'<div class="tech-line"><span>Vs máx 52sem:</span><span class="down">{data["dist_max_pct"]:.1f}%</span></div>'
    if "dist_min_pct" in data:
        tech += f'<div class="tech-line"><span>Vs mín 52sem:</span><span class="up">+{data["dist_min_pct"]:.1f}%</span></div>'
    op = ""
    if data.get("oportunidad"):
        op = f'<div class="op-badge">💡 {data["oportunidad"]}</div>'
    return (f'<div class="card"><div class="ticker">{ticker}</div>'
            f'<div class="name">{nombre}</div><div class="price">${precio:,.2f}</div>'
            f'<div class="change {clase}">{flecha} {cambio:+.2f}%</div>'
            f'<div class="tech">{tech}</div>{op}</div>')


def card_precio_ar(nombre, usd, ars=None, extra=""):
    """Tarjeta simple para precios locales en US$/Tn (y opcional $/Tn)."""
    precio = fmt_usd(usd) + " /Tn" if usd is not None else "s/c"
    sub = f'<div class="change flat">{fmt_ars(ars)} /Tn</div>' if ars is not None else ""
    ex = f'<div class="tech">{extra}</div>' if extra else ""
    return (f'<div class="card"><div class="name">{nombre}</div>'
            f'<div class="price">{precio}</div>{sub}{ex}</div>')


def tabla_granos(rosario, bahia, matba, cbot_yf):
    """Tabla comparativa de granos por mercado."""
    granos = ["Soja", "Maíz", "Trigo", "Girasol", "Sorgo"]
    filas = ""
    for g in granos:
        chi = matba.get("CHICAGO", {}).get(g, {}).get("precio")
        chi_txt = fmt_usd(chi) if chi is not None else "—"
        ros = rosario.get(g, {})
        ros_usd, ros_ars = ros.get("usd"), ros.get("ars")
        ros_txt = (fmt_usd(ros_usd) + f'<br><span class="mini">{fmt_ars(ros_ars)}</span>') if ros_usd else "—"
        bb = bahia.get(g, {}).get("usd")
        bb_txt = fmt_usd(bb) if bb is not None else "—"
        mt = matba.get("ROS", {}).get(g, {})
        mt_txt = (fmt_usd(mt.get("precio")) + f'<br><span class="mini">{mt.get("pos","")}</span>') if mt.get("precio") else "—"
        filas += (f'<tr><td class="grano">{g}</td>'
                  f'<td>{chi_txt}</td><td>{ros_txt}</td><td>{bb_txt}</td><td>{mt_txt}</td></tr>')
    return (f'<table class="cmp">'
            f'<thead><tr><th>Grano</th><th>Chicago (CBOT)</th><th>Pizarra Rosario</th>'
            f'<th>Bahía Blanca</th><th>MATBA Rofex (A3)</th></tr></thead>'
            f'<tbody>{filas}</tbody></table>'
            f'<div class="mini" style="margin-top:8px">Chicago, Bahía Blanca y MATBA Rofex en US$/Tn · '
            f'Rosario en US$/Tn y $/Tn · MATBA = futuro posición más cercana. '
            f'Fuentes: BCR, Bolsa de Cereales, Yahoo Finance.</div>')


def bloque_noticias(noticias):
    if not noticias:
        return '<div class="disclaimer">No se pudieron cargar noticias.</div>'
    html = ""
    for n in noticias:
        html += (f'<div class="news-item">'
                 f'<div class="news-title"><a href="{n["link"]}" target="_blank" rel="noopener">{n["titulo"]}</a></div>'
                 f'<div class="news-meta">{n["fuente"]} · {n["fecha"]}</div></div>')
    return html


# ============================================================
# GENERACIÓN DEL HTML
# ============================================================
def generar_html():
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    ahora = dt.datetime.now()
    hoy = f"{ahora.day} de {meses[ahora.month]} de {ahora.year} - {ahora:%H:%M} hs"

    print("[1/7] Cripto...")
    cripto = [(sym, name, obtener_cripto(cgid)) for cgid, sym, name in CRYPTOS]
    print("[2/7] Granos Chicago (Yahoo)...")
    cbot = [(sym, name, analizar_ticker(sym)) for sym, name, _ in GRANOS_CBOT]
    print("[3/7] Pizarra Rosario, Bahía Blanca y MATBA Rofex...")
    rosario = precios_rosario()
    bahia = precios_bahia_blanca()
    matba = precios_matba_rofex()
    print("[4/7] Energía y metales...")
    energia = [(sym, name, analizar_ticker(sym)) for sym, name, _ in ENERGIA_METALES]
    print("[5/7] Índices globales...")
    idx = [(sym, name, analizar_ticker(sym)) for sym, name in INDICES_GLOBALES]
    print("[6/7] ADRs argentinos...")
    adr = [(sym, name, analizar_ticker(sym)) for sym, name in ARGENTINA_ADRS]
    print("[7/7] Noticias agro y gobierno...")
    news_agro = obtener_noticias(NOTICIAS_AGRO)
    news_gob = obtener_noticias(NOTICIAS_GOB)

    oportunidades = []
    for lista in [cbot, energia, idx, adr]:
        for sym, name, data in lista:
            if data.get("oportunidad"):
                oportunidades.append(
                    f'<div class="alert-box opportunity"><div class="alert-title">💡 {name} ({sym})</div>'
                    f'<div class="alert-body">{data["oportunidad"]}</div></div>')
    oport_html = "\n".join(oportunidades) if oportunidades else \
        '<div class="disclaimer">No se detectaron alertas técnicas fuertes hoy.</div>'

    cards_cripto = "\n".join(card_activo(n, s, d) for s, n, d in cripto)
    cards_cbot = "\n".join(card_activo(n, s, d) for s, n, d in cbot)
    cards_energia = "\n".join(card_activo(n, s, d) for s, n, d in energia)
    cards_idx = "\n".join(card_activo(n, s, d) for s, n, d in idx)
    cards_adr = "\n".join(card_activo(n, s, d) for s, n, d in adr)

    cards_ros = "\n".join(card_precio_ar(g, rosario[g]["usd"], rosario[g]["ars"]) for g in rosario) \
        or '<div class="disclaimer">Rosario sin datos.</div>'
    cards_bahia = "\n".join(card_precio_ar(g, bahia[g]["usd"]) for g in bahia) \
        or '<div class="disclaimer">Bahía Blanca sin datos.</div>'
    cards_matba = "\n".join(
        card_precio_ar(f"{g} (fut. {matba['ROS'][g]['pos']})", matba["ROS"][g]["precio"])
        for g in matba.get("ROS", {})) or '<div class="disclaimer">MATBA Rofex sin datos.</div>'

    tabla_cmp = tabla_granos(rosario, bahia, matba, cbot)

    tc_txt = f'Dólar BNA divisa comprador: ${ESTADO["tc_bna"]:,.2f}' if ESTADO.get("tc_bna") else ""
    fecha_pizarra = f'Pizarra {ESTADO["fecha_rosario"]}' if ESTADO.get("fecha_rosario") else ""
    subhdr = (tc_txt + ("  ·  " + fecha_pizarra if tc_txt and fecha_pizarra else fecha_pizarra))

    return f'''<!DOCTYPE html>
<html lang="es-AR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reporte Diario — {hoy}</title>
<style>
:root {{ --bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;--green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff; }}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--text);padding:20px;line-height:1.5;}}
.header{{background:linear-gradient(135deg,#1e3a5f,#0d1117);padding:24px;border-radius:12px;margin-bottom:20px;border:1px solid var(--border);}}
.header h1{{font-size:24px;}} .header .date{{color:var(--muted);font-size:14px;margin-top:4px;}}
.header .tc{{color:var(--yellow);font-size:13px;margin-top:6px;}}
.tabs{{display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--border);flex-wrap:wrap;}}
.tab{{padding:12px 18px;background:transparent;border:none;color:var(--muted);cursor:pointer;font-size:14px;border-bottom:2px solid transparent;}}
.tab.active{{color:var(--blue);border-bottom-color:var(--blue);}}
.tab-content{{display:none;}} .tab-content.active{{display:block;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-bottom:24px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;}}
.card .ticker{{font-size:12px;color:var(--muted);}} .card .name{{font-size:15px;font-weight:600;margin:4px 0 8px;}}
.card .price{{font-size:20px;font-weight:700;margin-bottom:4px;}} .card .change{{font-size:13px;font-weight:500;}}
.up{{color:var(--green);}} .down{{color:var(--red);}} .flat{{color:var(--muted);}}
.card .tech{{margin-top:12px;padding-top:12px;border-top:1px solid var(--border);font-size:12px;color:var(--muted);}}
.card .tech-line{{display:flex;justify-content:space-between;margin-bottom:3px;}}
.op-badge{{margin-top:8px;padding:6px;background:rgba(63,185,80,0.15);border-radius:4px;font-size:12px;color:var(--green);}}
h2{{font-size:18px;margin:24px 0 12px;}} h3{{font-size:15px;margin:20px 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;}}
.cmp{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:8px;overflow:hidden;font-size:14px;}}
.cmp th,.cmp td{{padding:12px 14px;text-align:right;border-bottom:1px solid var(--border);}}
.cmp th{{background:rgba(88,166,255,0.08);color:var(--blue);font-weight:600;}}
.cmp th:first-child,.cmp td.grano{{text-align:left;}} .cmp td.grano{{font-weight:600;}}
.cmp tr:last-child td{{border-bottom:none;}}
.mini{{font-size:11px;color:var(--muted);}}
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
  <h1>📊 Reporte Diario de Mercados</h1>
  <div class="date">{hoy}</div>
  <div class="tc">{subhdr}</div>
</div>

<h2>🚨 Alertas y oportunidades detectadas</h2>
{oport_html}

<div class="tabs">
  <button class="tab active" onclick="showTab(event,'granos')">🌾 Granos</button>
  <button class="tab" onclick="showTab(event,'cripto')">🪙 Cripto</button>
  <button class="tab" onclick="showTab(event,'energia')">🛢️ Energía y metales</button>
  <button class="tab" onclick="showTab(event,'idx')">📈 Índices</button>
  <button class="tab" onclick="showTab(event,'ar')">🇦🇷 ADRs Argentina</button>
  <button class="tab" onclick="showTab(event,'news')">📰 Noticias</button>
</div>

<div id="granos" class="tab-content active">
  <h2>Comparativa de granos por mercado</h2>
  {tabla_cmp}
  <h3>Chicago (CBOT) — análisis técnico</h3><div class="grid">{cards_cbot}</div>
  <h3>Pizarra Rosario (BCR)</h3><div class="grid">{cards_ros}</div>
  <h3>Bahía Blanca</h3><div class="grid">{cards_bahia}</div>
  <h3>MATBA Rofex (A3) — futuros</h3><div class="grid">{cards_matba}</div>
</div>

<div id="cripto" class="tab-content"><div class="grid">{cards_cripto}</div></div>
<div id="energia" class="tab-content"><div class="grid">{cards_energia}</div></div>
<div id="idx" class="tab-content"><div class="grid">{cards_idx}</div></div>
<div id="ar" class="tab-content"><div class="grid">{cards_adr}</div></div>
<div id="news" class="tab-content">
  <h2>🌾 Agro argentino</h2>{bloque_noticias(news_agro)}
  <h2>🏛️ Gobierno · Política · Economía</h2>{bloque_noticias(news_gob)}
</div>

<div class="disclaimer">
  <strong>⚠️ Aviso:</strong> reporte informativo automatizado. No constituye asesoramiento
  financiero. Verificá siempre en tu plataforma antes de operar.
</div>
<div class="footer">Reporte generado {hoy} · Fuentes: CoinGecko, Yahoo Finance,
  BCR (Cámara Arbitral), Bolsa de Cereales, RSS de noticias.</div>

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
    print("\n=== Generando reporte diario ===")
    print(f"Fecha: {dt.datetime.now():%Y-%m-%d %H:%M}")
    print(f"Output: {OUTPUT_PATH}\n")
    html = generar_html()
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"\n✅ Reporte listo: {OUTPUT_PATH}\n")
