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
  - GANADERÍA ARGENTINA:
        * Mercado Agroganadero de Cañuelas (MAG) -> índices por categoría,
          INMAG / IGMAG / Índice Arrendamiento, cabezas, camiones, remate export
        * ROSGAN (invernada)       -> resultados de remates + informe semanal
        * CICCRA                   -> informe económico mensual (faena / gordo)
        * SAGyP / MAGyP            -> estadísticas oficiales de faena
        * IPCVA                    -> informe mensual de precios + novedades
        * Cámara Argentina Feedlot -> índices IIF / Ocupación + noticias
  - Energía y metales (WTI, Brent, gas, oro) via Yahoo Finance
  - Índices globales (S&P 500, NASDAQ, Dow, VIX) via Yahoo Finance
  - Argentina: ADRs (YPF, GGAL, BMA, PAM, TS, etc.)
  - Noticias: AGRO + GANADERÍA + GOBIERNO / política / economía (RSS)
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
NOTA SOBRE LAS FUENTES DE GANADERÍA
  Hay tres niveles de dato según lo que publica cada fuente en HTML plano:
    [A] Números en vivo  -> MAG Cañuelas (índices, categorías, cabezas)
    [B] Documento último -> CICCRA e IPCVA (se detecta y linkea el informe más
                            reciente; si hay PDF se intenta extraer la faena)
    [C] Solo enlace      -> CAF (los índices IIF/IO se dibujan por JavaScript
                            desde api.clicrural.com, no hay JSON público) y
                            SAGyP (el portal no expone los datos en el HTML).
  Todo lo que no se pueda resolver se muestra como "s/c" con el link directo.
------------------------------------------------------------------------------
Requisitos:
    pip install yfinance requests feedparser pandas beautifulsoup4 lxml
    (opcional, para leer la faena del PDF de CICCRA: pip install pdfplumber)
Autor: Claude para Juan Degiovanangelo
"""
import sys
import re
import io
import time
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

# Opcional: lectura de PDF para extraer faena de CICCRA
try:
    import pdfplumber
    HAY_PDF = True
except ImportError:
    HAY_PDF = False

# ============================================================
# CONFIGURACIÓN
# ============================================================
OUTPUT_PATH = Path(__file__).parent / "reporte_diario.html"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36"}
TIMEOUT = 25  # segundos por request

# ---- Fuentes de precios GRANOS ARGENTINA (editá las URLs si cambian) ----
URL_ROSARIO = "https://www.cac.bcr.com.ar/es/precios-de-pizarra"
URL_CAMARA  = "https://www.bolsadecereales.com/camara-arbitral"   # incluye Bahía Blanca
URL_MERCADO = "https://www.bolsadecereales.com/mercados"          # futuros MATBA Rofex (A3)

# ---- Fuentes de GANADERÍA ARGENTINA ----
URL_MAG         = "https://www.mercadoagroganadero.com.ar/dll/inicio.dll"
URL_MAG_EXPORT  = "https://www.mercadoagroganadero.com.ar/dll/precios-remates-tv-exportacion.html"
URL_ROSGAN      = "https://www.rosgan.com.ar/precios"
URL_ROSGAN_RSS  = "https://www.rosgan.com.ar/rss.xml"
URL_CICCRA      = "https://ciccra.com.ar/"
URL_IPCVA       = "https://www.ipcva.com.ar/"
URL_IPCVA_STATS = "https://ipcva.agrositio.com/estadisticas.php"
URL_CAF_IDX     = "https://feedlot.com.ar/informes-de-mercado/"
URL_CAF_IIF     = "https://www.api.clicrural.com/widget/caf/iif"
URL_CAF_IO      = "https://www.api.clicrural.com/widget/caf/showGraficaOcupacion"
URL_CAF_RSS     = "https://feedlot.com.ar/feed/"
URL_SAGYP       = "https://www.magyp.gob.ar/sitio/areas/ss_mercados_agropecuarios/carnes/"
URL_SAGYP_BOV   = "https://www.magyp.gob.ar/sitio/areas/bovinos/estadistica/"

# Categorías del MAG que queremos mostrar (orden de la tarjeta)
CATEGORIAS_MAG = ["Novillos", "Novillitos", "Vaquillonas", "Vacas", "Toros", "MEJ"]

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

# ---- Ganadería internacional (referencia Chicago) via Yahoo ----
GANADO_CME = [
    ("LE=F", "Novillo gordo (Live Cattle, CME)", "¢/lb"),
    ("GF=F", "Invernada (Feeder Cattle, CME)", "¢/lb"),
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

# ---- Noticias: GANADERÍA ----
NOTICIAS_GANADERIA = [
    (URL_ROSGAN_RSS, "ROSGAN"),
    (URL_CAF_RSS, "Cámara Argentina de Feedlot"),
    ("https://ciccra.com.ar/feed/", "CICCRA"),
    ("https://www.valorcarne.com.ar/feed/", "Valor Carne"),
    ("https://motivar.com.ar/feed/", "Motivar (sanidad animal)"),
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


def ar_int(s):
    """
    Convierte un CONTEO argentino a entero: '7.420' -> 7420 ; '206' -> 206.
    Distinto de ar_num(): acá el punto SIEMPRE es separador de miles, nunca
    decimal (son cabezas, camiones, DTe: no tienen parte decimal).
    """
    if s is None:
        return None
    s = re.sub(r"[^\d.]", "", str(s))
    if not s:
        return None
    try:
        return int(s.replace(".", ""))
    except ValueError:
        return None


def fmt_int(v):
    """Formatea un entero con separador de miles argentino: 7420 -> '7.420'."""
    return f"{v:,.0f}".replace(",", ".") if v is not None else "s/c"


def fmt_usd(v, dec=2):
    return f"US$ {v:,.{dec}f}" if v is not None else "s/c"


def fmt_ars(v, dec=0):
    """
    Pesos en formato argentino: 4183.9 -> '$4.183,90' (miles '.', decimal ',').
    Python formatea al revés, así que se intercambian los separadores.
    """
    if v is None:
        return "s/c"
    s = f"{v:,.{dec}f}"                       # '4,183.90'
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"${s}"


def get_soup(url):
    """GET + BeautifulSoup, o None si falla."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def texto_plano(soup):
    """Texto del documento colapsado a una sola línea (facilita los regex)."""
    return re.sub(r"\s+", " ", soup.get_text(" "))


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


def obtener_cripto(coingecko_id, reintentos=3):
    """CoinGecko con reintentos ante 429 (rate limit) usando backoff."""
    url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
    params = {"localization": "false", "tickers": "false",
              "community_data": "false", "developer_data": "false"}
    for intento in range(reintentos):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 429:  # demasiadas requests: esperar y reintentar
                time.sleep(15 * (intento + 1))
                continue
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
            if intento == reintentos - 1:
                return {"error": str(e)}
            time.sleep(5)
    return {"error": "429 rate limit"}


# ============================================================
# PRECIOS GRANOS ARGENTINA — SCRAPERS
# ============================================================
def precios_rosario():
    """
    Pizarra Rosario (Cámara Arbitral de Cereales - BCR).
    Devuelve {grano: {"ars": float, "usd": float}} en $/Tn y US$/Tn.
    """
    granos = [("Trigo", r"Trigo"), ("Maíz", r"Ma[íi]z"), ("Girasol", r"Girasol"),
              ("Soja", r"Soja"), ("Sorgo", r"Sorgo")]
    out = {}
    try:
        soup = get_soup(URL_ROSARIO)
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
    Devuelve {grano: {"usd": float}} en US$/Tn.
    """
    out = {}
    granos = [("Cebada Forrajera", r"Cebada Forrajera"), ("Trigo", r"Trigo"),
              ("Maíz", r"Ma[íi]z"), ("Soja", r"Soja"), ("Girasol", r"Girasol")]
    try:
        soup = get_soup(URL_CAMARA)
        txt = soup.get_text("\n")
        mb = re.search(r"Bah[íi]a Blanca", txt)
        if not mb:
            return out
        resto = txt[mb.end():]
        mfin = re.search(r"C[óo]rdoba|Descargar Cotizaci[óo]n", resto)
        seccion = resto[: mfin.start()] if mfin else resto[:2000]
        md = re.search(r"D[óo]lares", seccion)
        sub = seccion[md.start():] if md else seccion
        flat = re.sub(r"\s+", " ", sub)

        for nombre, pat in granos:
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
    Devuelve {"ROS": {...}, "CHICAGO": {...}} en US$/Tn (posición más cercana).
    """
    out = {"ROS": {}, "CHICAGO": {}}
    mapa = {"SOJA": "Soja", "MAIZ": "Maíz", "TRIGO": "Trigo"}
    try:
        r = requests.get(URL_MERCADO, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        try:
            tablas = pd.read_html(io.StringIO(r.text))
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
# GANADERÍA ARGENTINA — SCRAPERS
# ============================================================
def precios_mag():
    """
    Mercado Agroganadero de Cañuelas (ex Liniers).
    Devuelve:
      {
        "fecha": "30/06/2026",
        "categorias": {"Novillos": {"min":2300.0,"max":4670.0}, ...},   # $/kg vivo
        "indices":    {"INMAG": 4183.90, "IGMAG": 3464.71, "ARREND": 4296.46},
        "operacion":  {"camiones": 206, "cabezas": 7420, "cab_semana": 7420,
                       "porc_op": 100, "dte": 6922},
        "export": [{"categoria": "NOVILLOS Más de 430 kg", "min":7520,"max":8000}, ...]
      }
    Es la fuente más rica: el HTML de la home ya trae todos los índices.
    """
    out = {"fecha": None, "categorias": {}, "indices": {},
           "operacion": {}, "export": []}
    try:
        r = requests.get(URL_MAG, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        flat = texto_plano(soup)

        # --- Índices por categoría: "Novillos $ 2.300,00 / $ 4.670,00" ---
        for cat in CATEGORIAS_MAG:
            pat = rf"{cat}\s*\$\s*([\d\.]+,\d{{2}})\s*/\s*\$\s*([\d\.]+,\d{{2}})"
            m = re.search(pat, flat, re.I)
            if m:
                out["categorias"][cat] = {
                    "min": ar_num(m.group(1)),
                    "max": ar_num(m.group(2)),
                }

        # --- Índices generales (aparecen como "INMAG 4.183,90" y "4.183,90 INMAG") ---
        for clave, pat in [("INMAG", r"INMAG"),
                           ("IGMAG", r"IGMAG"),
                           ("ARREND", r"[ÍI]ndice\s*Arrend\.?")]:
            m = re.search(pat + r"\s*([\d\.]+,\d{2})", flat, re.I)
            if not m:
                m = re.search(r"([\d\.]+,\d{2})\s*" + pat, flat, re.I)
            if m:
                out["indices"][clave] = ar_num(m.group(1))

        # --- Operación del día ---
        for clave, pat in [("camiones", r"([\d\.]+)\s*CAMIONES"),
                           ("cabezas", r"([\d\.]+)\s*CABEZAS"),
                           ("cab_semana", r"([\d\.]+)\s*CAB\.?\s*SEMANA"),
                           ("dte", r"([\d\.]+)\s*DTe")]:
            m = re.search(pat, flat, re.I)
            if m:
                out["operacion"][clave] = ar_int(m.group(1))
        m = re.search(r"(\d{1,3})\s*%\s*OP", flat, re.I)
        if m:
            out["operacion"]["porc_op"] = float(m.group(1))

        # --- Fecha de la pizarra ---
        m = re.search(r"(\d{2}/\d{2}/\d{4})", flat)
        if m:
            out["fecha"] = m.group(1)

        # --- Remate especial de exportación (tabla) ---
        try:
            for df in pd.read_html(io.StringIO(r.text)):
                cols = " ".join(str(c) for c in df.columns).lower()
                if "mínimo" in cols or "minimo" in cols:
                    for _, row in df.iterrows():
                        vals = [str(v) for v in row.tolist()]
                        if len(vals) >= 3:
                            out["export"].append({
                                "categoria": vals[0],
                                "min": ar_num(vals[1]),
                                "max": ar_num(vals[2]),
                            })
                    break
        except Exception:
            pass

    except Exception as e:
        print(f"[WARN] MAG Cañuelas falló: {e}")
    return out


def precios_rosgan():
    """
    ROSGAN (Mercado Ganadero, BCR) — hacienda de INVERNADA.
    La grilla de resultados de /precios se arma por JavaScript, así que se
    intenta leer cualquier tabla que venga en el HTML y, si no hay, se cae al
    informe semanal del RSS. Devuelve:
      {"categorias": {cat: precio_$kg}, "remate": str|None, "informe": {...}|None}
    """
    out = {"categorias": {}, "remate": None, "informe": None}

    # --- Intento 1: tablas en /precios ---
    try:
        r = requests.get(URL_ROSGAN, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        flat = re.sub(r"\s+", " ", BeautifulSoup(r.text, "lxml").get_text(" "))

        m = re.search(r"Remate\s*N[°º]?\s*(\d+)", flat, re.I)
        if m:
            out["remate"] = f"Remate N° {m.group(1)}"

        cats = ["Terneros", "Terneras", "Novillitos", "Vaquillonas",
                "Novillos", "Vacas", "Toros"]
        for cat in cats:
            m = re.search(rf"{cat}\s*\$?\s*([\d\.]+,\d{{2}})", flat, re.I)
            if m:
                out["categorias"][cat] = ar_num(m.group(1))

        if not out["categorias"]:
            try:
                for df in pd.read_html(io.StringIO(r.text)):
                    df = df.astype(str)
                    for _, row in df.iterrows():
                        vals = [v for v in row.tolist() if v and v != "nan"]
                        if len(vals) >= 2:
                            for cat in cats:
                                if cat.lower() in vals[0].lower():
                                    p = ar_num(vals[-1])
                                    if p and 100 < p < 100000:
                                        out["categorias"].setdefault(cat, p)
            except Exception:
                pass
    except Exception as e:
        print(f"[WARN] ROSGAN precios falló: {e}")

    # --- Intento 2: informe semanal del RSS ---
    try:
        feed = feedparser.parse(URL_ROSGAN_RSS)
        if feed.entries:
            e = feed.entries[0]
            out["informe"] = {
                "titulo": e.get("title", "Informe semanal ROSGAN"),
                "link": e.get("link", URL_ROSGAN),
                "fecha": e.get("published", e.get("updated", "")),
            }
    except Exception as e:
        print(f"[WARN] ROSGAN RSS falló: {e}")

    return out


def ciccra_informe():
    """
    CICCRA — último Informe Económico mensual (faena, producción, consumo).
    Los archivos siguen el patrón: Inf-No-<nro>-<año>-<mes>.pdf|docx
    Se toma el de número más alto. Si es PDF y hay pdfplumber, se intenta
    extraer faena y consumo per cápita del texto.
    Devuelve {"nro","periodo","link","tipo","faena","consumo","novillo"}
    """
    out = {"nro": None, "periodo": None, "link": None, "tipo": None,
           "faena": None, "consumo": None, "novillo": None}
    try:
        soup = get_soup(URL_CICCRA)
        candidatos = []
        for a in soup.find_all("a", href=True):
            m = re.search(r"Inf-No-(\d+)([ab]?)-(\d{4})-([a-záéíóú]+)\.(pdf|docx)",
                          a["href"], re.I)
            if m:
                candidatos.append({
                    "nro": int(m.group(1)),
                    "sufijo": m.group(2),
                    "anio": m.group(3),
                    "mes": m.group(4),
                    "tipo": m.group(5).lower(),
                    "link": requests.compat.urljoin(URL_CICCRA, a["href"]),
                })
        if not candidatos:
            return out

        ult = sorted(candidatos, key=lambda c: (c["nro"], c["sufijo"]))[-1]
        out.update({
            "nro": f'{ult["nro"]}{ult["sufijo"]}',
            "periodo": f'{ult["mes"].capitalize()} {ult["anio"]}',
            "link": ult["link"],
            "tipo": ult["tipo"],
        })

        # --- Extracción best-effort de la faena desde el PDF ---
        if ult["tipo"] == "pdf" and HAY_PDF:
            try:
                r = requests.get(ult["link"], headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                    texto = " ".join((p.extract_text() or "")
                                     for p in pdf.pages[:3])
                texto = re.sub(r"\s+", " ", texto)

                m = re.search(r"faena[^.]{0,120}?([\d]{1,3}[\.,]\d{3}[\.,]\d{3})",
                              texto, re.I)
                if m:
                    out["faena"] = m.group(1)
                m = re.search(r"consumo[^.]{0,120}?([\d]{1,3}[,\.]\d)\s*kg", texto, re.I)
                if m:
                    out["consumo"] = m.group(1) + " kg/hab/año"
                m = re.search(r"novillo[^.]{0,80}?\$?\s*([\d\.]+,\d{2})", texto, re.I)
                if m:
                    out["novillo"] = m.group(1)
            except Exception as e:
                print(f"[WARN] CICCRA PDF no se pudo leer: {e}")
    except Exception as e:
        print(f"[WARN] CICCRA falló: {e}")
    return out


def ipcva_informe():
    """
    IPCVA — informe mensual de precios de la carne vacuna + novedades.
    Devuelve {"titulo","pdf","resumen","novedades":[{"titulo","link"}]}
    """
    out = {"titulo": None, "pdf": None, "resumen": None, "novedades": []}
    try:
        r = requests.get(URL_IPCVA, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, "lxml")
        flat = texto_plano(soup)

        m = re.search(r"Informe de precios de la carne vacuna del mes de ([^\n·|]{3,40})",
                      flat, re.I)
        if m:
            out["titulo"] = f"Informe de precios — {m.group(1).strip()}"

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "Informe%20Mensual%20de%20Precios" in href or \
               "Informe Mensual de Precios" in href:
                out["pdf"] = href
            elif re.search(r"Resumen[^/]*\.pdf", href, re.I) and not out["resumen"]:
                out["resumen"] = href

        vistos = set()
        for a in soup.find_all("a", href=True):
            if "/novedades/" in a["href"]:
                titulo = a.get_text(" ", strip=True)
                if len(titulo) > 25 and a["href"] not in vistos:
                    vistos.add(a["href"])
                    out["novedades"].append({
                        "titulo": titulo,
                        "link": requests.compat.urljoin(URL_IPCVA, a["href"]),
                    })
        out["novedades"] = out["novedades"][:6]
    except Exception as e:
        print(f"[WARN] IPCVA falló: {e}")
    return out


def caf_indices():
    """
    Cámara Argentina de Feedlot — IIF (Índice de Ingreso Feedlot) e
    IO (Índice de Ocupación).
    Los widgets viven en api.clicrural.com y dibujan el gráfico por JS, por lo
    que se hace un intento de leer el último valor de la serie embebida en el
    JavaScript. Si no aparece, se devuelve None y la tarjeta linkea al sitio.
    Devuelve {"iif": float|None, "io": float|None, "periodo": str|None}
    """
    out = {"iif": None, "io": None, "periodo": None}

    def ultimo_valor(url):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            txt = r.text
            # Series típicas de chart.js / highcharts: data: [12.3, 45.6, ...]
            m = re.findall(r"data\s*:\s*\[([^\]]+)\]", txt)
            for bloque in reversed(m):
                nums = re.findall(r"-?\d+(?:\.\d+)?", bloque)
                if len(nums) >= 3:
                    return float(nums[-1])
        except Exception as e:
            print(f"[WARN] CAF widget {url} falló: {e}")
        return None

    out["iif"] = ultimo_valor(URL_CAF_IIF)
    out["io"] = ultimo_valor(URL_CAF_IO)

    try:
        soup = get_soup(URL_CAF_IDX)
        flat = texto_plano(soup)
        m = re.search(r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
                      r"septiembre|octubre|noviembre|diciembre)\s+(\d{4})", flat, re.I)
        if m:
            out["periodo"] = f"{m.group(1).capitalize()} {m.group(2)}"
    except Exception as e:
        print(f"[WARN] CAF índices falló: {e}")
    return out


def sagyp_faena():
    """
    SAGyP / MAGyP — estadísticas oficiales de faena bovina.
    El portal no expone los números en HTML plano (son planillas descargables),
    así que se hace un intento de scrape y, si no hay dato, se devuelven los
    enlaces institucionales para consulta directa.
    Devuelve {"faena": str|None, "periodo": str|None, "links": [(nombre,url)]}
    """
    out = {
        "faena": None,
        "periodo": None,
        "links": [
            ("Mercados agropecuarios — Carnes", URL_SAGYP),
            ("Bovinos — Estadísticas oficiales", URL_SAGYP_BOV),
        ],
    }
    try:
        soup = get_soup(URL_SAGYP_BOV)
        flat = texto_plano(soup)
        m = re.search(r"faena[^.]{0,120}?([\d]{1,3}\.\d{3}\.\d{3})", flat, re.I)
        if m:
            out["faena"] = m.group(1)
        m = re.search(r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
                      r"septiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?(\d{4})",
                      flat, re.I)
        if m:
            out["periodo"] = f"{m.group(1).capitalize()} {m.group(2)}"
    except Exception as e:
        print(f"[WARN] SAGyP falló: {e}")
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


def card_info(nombre, valor, sub="", link=None, link_texto="Ver fuente"):
    """Tarjeta genérica: un título, un valor grande, un subtítulo y un link."""
    sub_html = f'<div class="change flat">{sub}</div>' if sub else ""
    link_html = (f'<div class="tech"><a class="src" href="{link}" target="_blank" '
                 f'rel="noopener">{link_texto} →</a></div>') if link else ""
    return (f'<div class="card"><div class="name">{nombre}</div>'
            f'<div class="price">{valor}</div>{sub_html}{link_html}</div>')


def card_categoria_mag(cat, rango):
    """Tarjeta de una categoría del MAG con su rango mín/máx en $/kg vivo."""
    mn, mx = rango.get("min"), rango.get("max")
    if mn is None and mx is None:
        return card_info(cat, "s/c", "Mercado Agroganadero")
    prom = (mn + mx) / 2 if (mn is not None and mx is not None) else (mn or mx)
    tech = (f'<div class="tech-line"><span>Mínimo:</span><span>{fmt_ars(mn)}</span></div>'
            f'<div class="tech-line"><span>Máximo:</span><span>{fmt_ars(mx)}</span></div>')
    return (f'<div class="card"><div class="ticker">MAG Cañuelas</div>'
            f'<div class="name">{cat}</div>'
            f'<div class="price">{fmt_ars(prom)}<span class="unit"> /kg vivo</span></div>'
            f'<div class="change flat">promedio del rango</div>'
            f'<div class="tech">{tech}</div></div>')


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


def tabla_mag(mag):
    """Tabla de índices por categoría del Mercado Agroganadero (Cañuelas)."""
    cats = mag.get("categorias", {})
    if not cats:
        return ('<div class="disclaimer">No se pudieron leer los índices del '
                f'Mercado Agroganadero. <a class="src" href="{URL_MAG}" target="_blank" '
                'rel="noopener">Ver sitio →</a></div>')
    filas = ""
    for cat in CATEGORIAS_MAG:
        if cat not in cats:
            continue
        mn, mx = cats[cat].get("min"), cats[cat].get("max")
        prom = (mn + mx) / 2 if (mn is not None and mx is not None) else (mn or mx)
        filas += (f'<tr><td class="grano">{cat}</td>'
                  f'<td>{fmt_ars(mn)}</td><td>{fmt_ars(mx)}</td>'
                  f'<td><strong>{fmt_ars(prom)}</strong></td></tr>')
    return (f'<table class="cmp">'
            f'<thead><tr><th>Categoría</th><th>Mínimo ($/kg)</th>'
            f'<th>Máximo ($/kg)</th><th>Promedio</th></tr></thead>'
            f'<tbody>{filas}</tbody></table>')


def tabla_mag_export(mag):
    """Tabla del remate especial de exportación del MAG (si está publicada)."""
    filas_src = [f for f in mag.get("export", []) if f.get("min") or f.get("max")]
    if not filas_src:
        return ""
    filas = "".join(
        f'<tr><td class="grano">{f["categoria"]}</td>'
        f'<td>{fmt_ars(f["min"])}</td><td>{fmt_ars(f["max"])}</td></tr>'
        for f in filas_src)
    return (f'<h3>Remate especial de exportación (MAG TV)</h3>'
            f'<table class="cmp"><thead><tr><th>Categoría</th>'
            f'<th>Mínimo ($/kg)</th><th>Máximo ($/kg)</th></tr></thead>'
            f'<tbody>{filas}</tbody></table>')


def bloque_noticias(noticias):
    if not noticias:
        return '<div class="disclaimer">No se pudieron cargar noticias.</div>'
    html = ""
    for n in noticias:
        html += (f'<div class="news-item">'
                 f'<div class="news-title"><a href="{n["link"]}" target="_blank" rel="noopener">{n["titulo"]}</a></div>'
                 f'<div class="news-meta">{n["fuente"]} · {n["fecha"]}</div></div>')
    return html


def bloque_ganaderia(mag, rosgan, ciccra, ipcva, caf, sagyp, cards_cme):
    """Arma toda la pestaña de ganadería."""
    # --- MAG: encabezado operativo ---
    op = mag.get("operacion", {})
    idx = mag.get("indices", {})
    fecha_mag = mag.get("fecha") or "s/f"

    cards_idx_mag = "\n".join([
        card_info("INMAG", fmt_ars(idx.get("INMAG"), 2),
                  "Índice Mercado Agroganadero (ex INML)", URL_MAG, "MAG Cañuelas"),
        card_info("IGMAG", fmt_ars(idx.get("IGMAG"), 2),
                  "Índice General MAG (ex IGML)", URL_MAG, "MAG Cañuelas"),
        card_info("Índice Arrendamiento", fmt_ars(idx.get("ARREND"), 2),
                  "Referencia para arrendamientos rurales", URL_MAG, "MAG Cañuelas"),
    ])

    cards_op_mag = "\n".join([
        card_info("Cabezas ingresadas", fmt_int(op.get("cabezas")), f"Pizarra {fecha_mag}"),
        card_info("Camiones", fmt_int(op.get("camiones")), f"Pizarra {fecha_mag}"),
        card_info("Cabezas en la semana", fmt_int(op.get("cab_semana")), "Acumulado semanal"),
        card_info("% operado", f'{op.get("porc_op"):.0f}%' if op.get("porc_op") is not None
                  else "s/c", "Porcentaje de la oferta vendida"),
        card_info("DTe a MAG", fmt_int(op.get("dte")),
                  "Documentos de tránsito electrónico"),
    ])

    cards_cat_mag = "\n".join(card_categoria_mag(c, mag["categorias"][c])
                              for c in CATEGORIAS_MAG if c in mag.get("categorias", {}))
    if not cards_cat_mag:
        cards_cat_mag = ('<div class="disclaimer">Sin índices por categoría hoy. '
                         f'<a class="src" href="{URL_MAG}" target="_blank" rel="noopener">Ver MAG →</a></div>')

    # --- ROSGAN (invernada) ---
    if rosgan.get("categorias"):
        cards_rosgan = "\n".join(
            card_info(cat, fmt_ars(precio) + '<span class="unit"> /kg</span>',
                      rosgan.get("remate") or "Último remate", URL_ROSGAN, "ROSGAN")
            for cat, precio in rosgan["categorias"].items())
    else:
        inf = rosgan.get("informe")
        cards_rosgan = card_info(
            "Invernada ROSGAN",
            "s/c",
            "La grilla de resultados se carga por JavaScript; abrí el sitio para el detalle",
            URL_ROSGAN, "Resultados históricos ROSGAN")
        if inf:
            cards_rosgan += card_info("Informe semanal", inf["titulo"][:60],
                                      inf.get("fecha", ""), inf["link"], "Leer informe")

    # --- CICCRA (faena / gordo) ---
    ciccra_extra = ""
    if ciccra.get("faena"):
        ciccra_extra += f'<div class="tech-line"><span>Faena informada:</span><span>{ciccra["faena"]} cab.</span></div>'
    if ciccra.get("consumo"):
        ciccra_extra += f'<div class="tech-line"><span>Consumo per cápita:</span><span>{ciccra["consumo"]}</span></div>'
    if ciccra.get("novillo"):
        ciccra_extra += f'<div class="tech-line"><span>Novillo (informe):</span><span>${ciccra["novillo"]}</span></div>'

    if ciccra.get("link"):
        # El aviso solo tiene sentido si el informe es PDF y falta la librería
        nota_pdf = ("Instalá pdfplumber para extraer la faena del PDF automáticamente."
                    if (ciccra["tipo"] == "pdf" and not HAY_PDF) else "")
        if ciccra["tipo"] == "docx":
            nota_pdf = "Este mes CICCRA publicó el informe en Word, no en PDF."
        card_ciccra = (
            f'<div class="card"><div class="ticker">CICCRA</div>'
            f'<div class="name">Informe Económico N° {ciccra["nro"]}</div>'
            f'<div class="price">{ciccra["periodo"]}</div>'
            f'<div class="change flat">Faena, producción y consumo de carne vacuna</div>'
            f'<div class="tech">{ciccra_extra}'
            f'<a class="src" href="{ciccra["link"]}" target="_blank" rel="noopener">'
            f'Descargar informe ({ciccra["tipo"].upper()}) →</a>'
            f'<div class="mini">{nota_pdf}</div></div></div>')
    else:
        card_ciccra = card_info("CICCRA", "s/c", "No se detectó el último informe",
                                URL_CICCRA, "Ver informes CICCRA")

    # --- IPCVA ---
    ipcva_links = ""
    if ipcva.get("pdf"):
        ipcva_links += (f'<a class="src" href="{ipcva["pdf"]}" target="_blank" '
                        f'rel="noopener">Informe completo (PDF) →</a><br>')
    if ipcva.get("resumen"):
        ipcva_links += (f'<a class="src" href="{ipcva["resumen"]}" target="_blank" '
                        f'rel="noopener">Resumen ejecutivo →</a><br>')
    ipcva_links += (f'<a class="src" href="{URL_IPCVA_STATS}" target="_blank" '
                    f'rel="noopener">Estadísticas IPCVA →</a>')
    card_ipcva = (
        f'<div class="card"><div class="ticker">IPCVA</div>'
        f'<div class="name">{ipcva.get("titulo") or "Informe mensual de precios"}</div>'
        f'<div class="price" style="font-size:15px">Precios al consumidor y exportación</div>'
        f'<div class="tech">{ipcva_links}</div></div>')

    # --- CAF (feedlot) ---
    card_caf_iif = card_info(
        "IIF — Índice de Ingreso Feedlot",
        f'{caf["iif"]:,.1f}' if caf.get("iif") is not None else "s/c",
        caf.get("periodo") or "Ingresos sobre capacidad de encierre",
        URL_CAF_IDX, "Ver gráfico CAF")
    card_caf_io = card_info(
        "IO — Índice de Ocupación",
        f'{caf["io"]:,.1f}%' if caf.get("io") is not None else "s/c",
        caf.get("periodo") or "Animales encerrados sobre capacidad total",
        URL_CAF_IDX, "Ver gráfico CAF")
    card_caf_mb = card_info(
        "Margen bruto y Cuota 481",
        "Informes",
        "Márgenes del engorde a corral y cupo de exportación",
        URL_CAF_IDX, "Ver informes CAF")

    # --- SAGyP / MAGyP ---
    cards_sagyp = card_info(
        "Faena bovina oficial",
        sagyp.get("faena") or "s/c",
        sagyp.get("periodo") or "Serie oficial publicada por la Secretaría",
        URL_SAGYP_BOV, "Estadísticas SAGyP")
    cards_sagyp += card_info(
        "Mercados agropecuarios — Carnes",
        "Portal oficial",
        "Precios, faena, exportaciones y normativa",
        URL_SAGYP, "Ir al portal SAGyP")

    return f'''
  <h2>Mercado Agroganadero de Cañuelas (MAG) — pizarra {fecha_mag}</h2>
  {tabla_mag(mag)}
  {tabla_mag_export(mag)}

  <h3>Índices del MAG</h3><div class="grid">{cards_idx_mag}</div>
  <h3>Operación del día</h3><div class="grid">{cards_op_mag}</div>
  <h3>Detalle por categoría</h3><div class="grid">{cards_cat_mag}</div>

  <h2>Invernada — ROSGAN (BCR)</h2>
  <div class="grid">{cards_rosgan}</div>

  <h2>Faena y hacienda gorda — CICCRA y SAGyP</h2>
  <div class="grid">{card_ciccra}
  {cards_sagyp}</div>

  <h2>Engorde a corral — Cámara Argentina de Feedlot</h2>
  <div class="grid">{card_caf_iif}
  {card_caf_io}
  {card_caf_mb}</div>

  <h2>Institucional — IPCVA</h2>
  <div class="grid">{card_ipcva}</div>

  <h2>Referencia internacional — CME Chicago</h2>
  <div class="grid">{cards_cme}</div>

  <div class="disclaimer">
    <strong>Sobre las fuentes:</strong> MAG Cañuelas publica sus índices en HTML y se leen
    en vivo. CICCRA e IPCVA publican informes mensuales (se detecta y linkea el más reciente).
    Los índices IIF/IO de la CAF y las series de la SAGyP se cargan por JavaScript o planilla
    descargable, por lo que pueden aparecer como "s/c" con el enlace directo a la fuente.
  </div>
'''


# ============================================================
# GENERACIÓN DEL HTML
# ============================================================
def generar_html():
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    ahora = dt.datetime.now()
    hoy = (f"{dias[ahora.weekday()]} {ahora.day} de {meses[ahora.month]} "
           f"de {ahora.year} - {ahora:%H:%M} hs (ARG)")

    print("[1/10] Cripto...")
    cripto = [(sym, name, obtener_cripto(cgid)) for cgid, sym, name in CRYPTOS]

    print("[2/10] Granos Chicago (Yahoo)...")
    cbot = [(sym, name, analizar_ticker(sym)) for sym, name, _ in GRANOS_CBOT]

    print("[3/10] Pizarra Rosario, Bahía Blanca y MATBA Rofex...")
    rosario = precios_rosario()
    bahia = precios_bahia_blanca()
    matba = precios_matba_rofex()

    print("[4/10] Ganadería: Mercado Agroganadero de Cañuelas...")
    mag = precios_mag()

    print("[5/10] Ganadería: ROSGAN, CICCRA, IPCVA, CAF y SAGyP...")
    rosgan = precios_rosgan()
    ciccra = ciccra_informe()
    ipcva = ipcva_informe()
    caf = caf_indices()
    sagyp = sagyp_faena()
    cme = [(sym, name, analizar_ticker(sym)) for sym, name, _ in GANADO_CME]

    print("[6/10] Energía y metales...")
    energia = [(sym, name, analizar_ticker(sym)) for sym, name, _ in ENERGIA_METALES]

    print("[7/10] Índices globales...")
    idx = [(sym, name, analizar_ticker(sym)) for sym, name in INDICES_GLOBALES]

    print("[8/10] ADRs argentinos...")
    adr = [(sym, name, analizar_ticker(sym)) for sym, name in ARGENTINA_ADRS]

    print("[9/10] Noticias agro, ganadería y gobierno...")
    news_agro = obtener_noticias(NOTICIAS_AGRO)
    news_gan = obtener_noticias(NOTICIAS_GANADERIA, por_feed=3, limite=15)
    news_gob = obtener_noticias(NOTICIAS_GOB)

    # Novedades del IPCVA (no tiene RSS, se scrapean de la home)
    for n in ipcva.get("novedades", []):
        news_gan.append({"titulo": n["titulo"], "link": n["link"],
                         "fuente": "IPCVA", "fecha": ""})

    print("[10/10] Armando HTML...")
    oportunidades = []
    for lista in [cbot, cme, energia, idx, adr]:
        for sym, name, data in lista:
            if data.get("oportunidad"):
                oportunidades.append(
                    f'<div class="alert-box opportunity"><div class="alert-title">💡 {name} ({sym})</div>'
                    f'<div class="alert-body">{data["oportunidad"]}</div></div>')
    oport_html = "\n".join(oportunidades) if oportunidades else \
        '<div class="disclaimer">No se detectaron alertas técnicas fuertes hoy.</div>'

    cards_cripto = "\n".join(card_activo(n, s, d) for s, n, d in cripto)
    cards_cbot = "\n".join(card_activo(n, s, d) for s, n, d in cbot)
    cards_cme = "\n".join(card_activo(n, s, d) for s, n, d in cme)
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
    seccion_ganaderia = bloque_ganaderia(mag, rosgan, ciccra, ipcva, caf, sagyp, cards_cme)

    tc_txt = f'Dólar BNA divisa comprador: ${ESTADO["tc_bna"]:,.2f}' if ESTADO.get("tc_bna") else ""
    fecha_pizarra = f'Pizarra {ESTADO["fecha_rosario"]}' if ESTADO.get("fecha_rosario") else ""
    partes = [p for p in [tc_txt, fecha_pizarra] if p]
    if mag.get("indices", {}).get("INMAG"):
        partes.append(f'INMAG {mag["indices"]["INMAG"]:,.2f}')
    subhdr = "  ·  ".join(partes)

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
.card .unit{{font-size:12px;font-weight:400;color:var(--muted);}}
.up{{color:var(--green);}} .down{{color:var(--red);}} .flat{{color:var(--muted);}}
.card .tech{{margin-top:12px;padding-top:12px;border-top:1px solid var(--border);font-size:12px;color:var(--muted);}}
.card .tech-line{{display:flex;justify-content:space-between;margin-bottom:3px;}}
a.src{{color:var(--blue);text-decoration:none;}} a.src:hover{{text-decoration:underline;}}
.op-badge{{margin-top:8px;padding:6px;background:rgba(63,185,80,0.15);border-radius:4px;font-size:12px;color:var(--green);}}
h2{{font-size:18px;margin:24px 0 12px;}} h3{{font-size:15px;margin:20px 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;}}
.cmp{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:8px;overflow:hidden;font-size:14px;margin-bottom:8px;}}
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
  <h1>📊 Reporte de Mercados</h1>
  <div class="date">{hoy} · Se actualiza cada hora automáticamente</div>
  <div class="tc">{subhdr}</div>
</div>

<h2>🚨 Alertas y oportunidades detectadas</h2>
{oport_html}

<div class="tabs">
  <button class="tab active" onclick="showTab(event,'granos')">🌾 Granos</button>
  <button class="tab" onclick="showTab(event,'ganaderia')">🐄 Ganadería</button>
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

<div id="ganaderia" class="tab-content">
{seccion_ganaderia}
</div>

<div id="cripto" class="tab-content"><div class="grid">{cards_cripto}</div></div>
<div id="energia" class="tab-content"><div class="grid">{cards_energia}</div></div>
<div id="idx" class="tab-content"><div class="grid">{cards_idx}</div></div>
<div id="ar" class="tab-content"><div class="grid">{cards_adr}</div></div>

<div id="news" class="tab-content">
  <h2>🌾 Agro argentino</h2>{bloque_noticias(news_agro)}
  <h2>🐄 Ganadería y carnes</h2>{bloque_noticias(news_gan)}
  <h2>🏛️ Gobierno · Política · Economía</h2>{bloque_noticias(news_gob)}
</div>

<div class="disclaimer">
  <strong>⚠️ Aviso:</strong> reporte informativo automatizado. No constituye asesoramiento
  financiero. Verificá siempre en tu plataforma (Quantfury, Cocos, Binance) antes de operar.
</div>

<div class="footer">Reporte generado {hoy} · Fuentes: CoinGecko, Yahoo Finance,
  BCR (Cámara Arbitral), Bolsa de Cereales, Mercado Agroganadero de Cañuelas, ROSGAN,
  CICCRA, IPCVA, SAGyP/MAGyP, Cámara Argentina de Feedlot y RSS de noticias.</div>

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
