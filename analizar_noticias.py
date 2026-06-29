import feedparser
import requests
import smtplib
import json
import os
import hashlib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_USER        = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD    = os.environ.get("GMAIL_PASSWORD", "")
EMAIL_DESTINO     = os.environ.get("EMAIL_DESTINO", "")

PROCESSED_FILE = "processed_ids.json"

# ─────────────────────────────────────────────
# FEEDS RSS
# ─────────────────────────────────────────────
FEEDS = {
    "Argentina": [
        "https://www.infobae.com/feeds/rss/economia.xml",
        "https://www.lanacion.com.ar/economia/feed/",
        "https://www.ambito.com/rss/pages/economia.xml",
        "https://www.cronista.com/rss/economia/",
    ],
    "Mercados Globales": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/marketsNews",
    ],
    "Geopolítica": [
        "https://feeds.reuters.com/Reuters/worldNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    ],
    "Tech / IA": [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
    ],
}

# ─────────────────────────────────────────────
# KEYWORDS
# ─────────────────────────────────────────────
KEYWORDS = [
    "dólar", "dolar", "cepo", "bcra", "reservas", "deuda", "milei",
    "inflación", "inflacion", "peso", "afip", "arca", "exportaciones",
    "fed", "tasa", "s&p", "nasdaq", "recesión", "recesion",
    "reserva federal", "powell", "treasury", "bonos", "bolsa",
    "commodities", "soja", "petróleo", "petroleo", "oro",
    "guerra", "sanciones", "opep", "aranceles", "trump", "china",
    "rusia", "ucrania", "medio oriente", "taiwan",
    "openai", "anthropic", "nvidia", "inteligencia artificial",
    "ia", "chip", "semiconductor", "google deepmind", "llm",
]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def cargar_procesados():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def guardar_procesados(ids):
    ids_lista = list(ids)[-500:]
    with open(PROCESSED_FILE, "w") as f:
        json.dump(ids_lista, f)

def id_noticia(entry):
    base = entry.get("link", "") or entry.get("title", "")
    return hashlib.md5(base.encode()).hexdigest()

def es_reciente(entry, horas=24):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return True
    fecha = datetime(*published[:6])
    return datetime.utcnow() - fecha < timedelta(hours=horas)

def es_relevante(titulo, descripcion):
    texto = (titulo + " " + descripcion).lower()
    return any(k in texto for k in KEYWORDS)

# ─────────────────────────────────────────────
# CLAUDE HAIKU — análisis de noticia
# ─────────────────────────────────────────────
PROMPT_SISTEMA = (
    "Sos un analista financiero y geopolítico senior. "
    "Tu trabajo es leer noticias y explicar el impacto real en los mercados, "
    "no repetir lo que dice la noticia. "
    "Reglas: nunca uses frases vagas como 'podría impactar' o 'se espera que'. "
    "Si no hay impacto claro en mercados, asigná RELEVANCIA: Baja. "
    "El análisis debe responder: ¿qué hace el mercado con esto? ¿quién gana, quién pierde? "
    "Mencioná siempre si hay correlación con Argentina cuando sea relevante."
)

def analizar_con_claude(titulo, fuente, descripcion):
    print(f"  DEBUG key: {ANTHROPIC_API_KEY[:10]}...")  # agregar esta línea
    prompt = (
        f"Noticia: {titulo}\n"
        f"Fuente: {fuente}\n"
        f"Contenido: {descripcion[:800]}\n\n"
        "Respondé EXACTAMENTE en este formato, sin texto extra:\n\n"
        "RESUMEN: (máximo 2 líneas, qué pasó)\n"
        "IMPACTO MERCADO: (qué movió o va a mover: dólar, bonos, acciones — ser específico)\n"
        "ARGENTINA: (cómo afecta; si no aplica escribí 'Sin impacto directo')\n"
        "RELEVANCIA: (Alta / Media / Baja)\n"
    )

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 400,
        "system": PROMPT_SISTEMA,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        return data["content"][0]["text"].strip()
    except Exception as e:
        print(f"  ⚠ Error Claude: {e}")
        return None

def parse_relevancia(analisis_texto):
    for linea in analisis_texto.splitlines():
        if linea.upper().startswith("RELEVANCIA"):
            if "ALTA" in linea.upper():
                return "Alta"
            if "MEDIA" in linea.upper():
                return "Media"
    return "Baja"

# ─────────────────────────────────────────────
# EMAIL HTML
# ─────────────────────────────────────────────
def construir_email(noticias_analizadas):
    fecha = datetime.now().strftime("%A %d de %B, %Y").capitalize()

    altas  = [n for n in noticias_analizadas if n["relevancia"] == "Alta"]
    medias = [n for n in noticias_analizadas if n["relevancia"] == "Media"]

    def bloque_noticias(lista, color, emoji, titulo_seccion):
        if not lista:
            return ""
        html = f"""
        <tr><td style="padding:24px 32px 8px">
            <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:2px;
                      color:{color};text-transform:uppercase">{emoji} {titulo_seccion}</p>
            <hr style="border:none;border-top:1px solid {color};margin:8px 0 0">
        </td></tr>"""
        for n in lista:
            lineas = {l.split(":")[0].strip().upper(): ":".join(l.split(":")[1:]).strip()
                      for l in n["analisis"].splitlines() if ":" in l}
            resumen   = lineas.get("RESUMEN", "")
            impacto   = lineas.get("IMPACTO MERCADO", "")
            argentina = lineas.get("ARGENTINA", "")

            html += f"""
        <tr><td style="padding:16px 32px 20px;border-bottom:1px solid #f0f0f0">
            <p style="margin:0 0 6px;font-size:15px;font-weight:700;color:#1a1a1a;
                      line-height:1.4">{n['titulo']}</p>
            <p style="margin:0 0 12px;font-size:11px;color:#888">
                {n['fuente']} &nbsp;·&nbsp; {n['categoria']}
            </p>
            <table style="width:100%;border-collapse:collapse">
                <tr>
                    <td style="width:50%;padding:8px 12px;background:#f8f9fa;
                               border-radius:6px;vertical-align:top">
                        <p style="margin:0 0 3px;font-size:10px;font-weight:700;
                                  color:#555;text-transform:uppercase;letter-spacing:1px">
                            Resumen</p>
                        <p style="margin:0;font-size:13px;color:#333;line-height:1.5">
                            {resumen}</p>
                    </td>
                    <td style="width:2%"></td>
                    <td style="width:48%;padding:8px 12px;background:#f8f9fa;
                               border-radius:6px;vertical-align:top">
                        <p style="margin:0 0 3px;font-size:10px;font-weight:700;
                                  color:#555;text-transform:uppercase;letter-spacing:1px">
                            Impacto mercado</p>
                        <p style="margin:0;font-size:13px;color:#333;line-height:1.5">
                            {impacto}</p>
                    </td>
                </tr>
            </table>
            {"" if argentina in ("Sin impacto directo", "") else f'''
            <p style="margin:12px 0 0;font-size:12px;color:#2c5282;background:#ebf4ff;
                      padding:8px 12px;border-radius:6px;border-left:3px solid #3182ce">
                🇦🇷 <b>Argentina:</b> {argentina}</p>'''}
            <p style="margin:12px 0 0">
                <a href="{n['link']}" style="font-size:12px;color:#3182ce;
                          text-decoration:none;font-weight:600">
                    Ver noticia completa →</a></p>
        </td></tr>"""
        return html

    cuerpo_altas  = bloque_noticias(altas,  "#e74c3c", "🔴", f"Alta relevancia ({len(altas)})")
    cuerpo_medias = bloque_noticias(medias, "#f39c12", "🟡", f"Media relevancia ({len(medias)})")

    sin_noticias = ""
    if not altas and not medias:
        sin_noticias = """
        <tr><td style="padding:40px 32px;text-align:center;color:#888">
            Sin noticias de alta o media relevancia en las últimas 24 horas.
        </td></tr>"""

    total = len(noticias_analizadas)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,
             BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<table width="100%" style="max-width:680px;margin:32px auto;background:#fff;
       border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08)">

    <tr><td style="background:#1a1a2e;padding:28px 32px">
        <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:3px;
                  color:#4a9eff;text-transform:uppercase">Briefing diario</p>
        <p style="margin:6px 0 0;font-size:22px;font-weight:800;color:#fff">{fecha}</p>
        <p style="margin:6px 0 0;font-size:12px;color:#888">
            {len(altas)} alertas altas · {len(medias)} alertas medias · 
            {total} noticias analizadas</p>
    </td></tr>

    {cuerpo_altas}
    {cuerpo_medias}
    {sin_noticias}

    <tr><td style="padding:20px 32px;background:#f8f9fa;border-top:1px solid #eee">
        <p style="margin:0;font-size:11px;color:#aaa;text-align:center">
            Generado automáticamente · Análisis por Claude Haiku</p>
    </td></tr>

</table></body></html>"""
    return html

def enviar_email(html, gmail_user, gmail_password, destinatario, n_altas):
    emoji = "🔴" if n_altas > 0 else "📊"
    asunto = f"{emoji} Briefing diario — {datetime.now().strftime('%d/%m/%Y')}"
    if n_altas > 0:
        asunto += f" ({n_altas} alertas altas)"

    msg = MIMEMultipart("alternative")
    msg["From"]    = gmail_user
    msg["To"]      = destinatario
    msg["Subject"] = asunto
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, destinatario, msg.as_string())
    print(f"✅ Email enviado a {destinatario}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print(f"\n{'='*50}")
    print(f"  Briefing diario — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}\n")

    procesados = cargar_procesados()
    noticias_analizadas = []

    for categoria, urls in FEEDS.items():
        print(f"📡 Procesando: {categoria}")
        for url in urls:
            try:
                feed = feedparser.parse(url)
                fuente = feed.feed.get("title", url)
                nuevas = 0

                for entry in feed.entries:
                    nid = id_noticia(entry)
                    if nid in procesados:
                        continue
                    if not es_reciente(entry, horas=24):
                        continue

                    titulo      = entry.get("title", "Sin título")
                    descripcion = entry.get("summary", "") or entry.get("description", "")
                    link        = entry.get("link", "")

                    if not es_relevante(titulo, descripcion):
                        procesados.add(nid)
                        continue

                    print(f"  🔍 Analizando: {titulo[:70]}...")
                    analisis = analizar_con_claude(titulo, fuente, descripcion)
                    time.sleep(1)

                    if analisis:
                        relevancia = parse_relevancia(analisis)
                        if relevancia in ("Alta", "Media"):
                            noticias_analizadas.append({
                                "titulo":     titulo,
                                "fuente":     fuente,
                                "link":       link,
                                "categoria":  categoria,
                                "analisis":   analisis,
                                "relevancia": relevancia,
                            })
                            print(f"    → {relevancia}")

                    procesados.add(nid)
                    nuevas += 1

                print(f"  ✓ {fuente}: {nuevas} nuevas procesadas")

            except Exception as e:
                print(f"  ⚠ Error con {url}: {e}")

    guardar_procesados(procesados)

    orden = {"Alta": 0, "Media": 1, "Baja": 2}
    noticias_analizadas.sort(key=lambda x: orden.get(x["relevancia"], 9))

    print(f"\n📊 Resultado: {len(noticias_analizadas)} noticias relevantes")
    altas = sum(1 for n in noticias_analizadas if n["relevancia"] == "Alta")

    html = construir_email(noticias_analizadas)
    enviar_email(html, GMAIL_USER, GMAIL_PASSWORD, EMAIL_DESTINO, altas)

if __name__ == "__main__":
    main()
