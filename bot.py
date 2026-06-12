import os
import logging
import sqlite3
from datetime import date, timedelta
import google.generativeai as genai
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    PreCheckoutQueryHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
FREE_DAILY_LIMIT = 3
STARS_PRICE = 299
REFERRAL_COMMISSION = 60
DB_PATH = "creatorai.db"

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# ── States ────────────────────────────────────────────────────────────────────
WAITING_INPUT = 1

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            uses           INTEGER DEFAULT 0,
            last_date      TEXT    DEFAULT '',
            premium_until  TEXT    DEFAULT '',
            referred_by    INTEGER DEFAULT NULL,
            stars_earned   INTEGER DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            nicho          TEXT    DEFAULT '',
            red_social     TEXT    DEFAULT '',
            tono           TEXT    DEFAULT ''
        )
    """)
    con.commit()
    con.close()

def get_user(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT uses, last_date, premium_until, referred_by, stars_earned, referral_count, nicho, red_social, tono FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if row is None:
        return {"uses": 0, "last_date": "", "premium_until": "", "referred_by": None, "stars_earned": 0, "referral_count": 0, "nicho": "", "red_social": "", "tono": ""}
    return {"uses": row[0], "last_date": row[1], "premium_until": row[2], "referred_by": row[3], "stars_earned": row[4], "referral_count": row[5], "nicho": row[6] or "", "red_social": row[7] or "", "tono": row[8] or ""}

def register_user(user_id, referred_by=None):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referred_by))
    con.commit()
    con.close()

def save_profile(user_id, nicho, red_social, tono):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE users SET nicho=?, red_social=?, tono=? WHERE user_id=?", (nicho, red_social, tono, user_id))
    con.commit()
    con.close()

def record_use(user_id):
    user = get_user(user_id)
    today = str(date.today())
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    uses = 1 if user["last_date"] != today else user["uses"] + 1
    cur.execute("UPDATE users SET uses=?, last_date=? WHERE user_id=?", (uses, today, user_id))
    con.commit()
    con.close()

def set_premium(user_id, until):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE users SET premium_until=? WHERE user_id=?", (until, user_id))
    con.commit()
    con.close()

def add_commission(referrer_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE users SET stars_earned=stars_earned+?, referral_count=referral_count+1 WHERE user_id=?", (REFERRAL_COMMISSION, referrer_id))
    con.commit()
    con.close()

def is_premium(user):
    pu = user.get("premium_until", "")
    if not pu:
        return False
    try:
        return date.fromisoformat(pu) >= date.today()
    except Exception:
        return False

def can_use(user_id):
    user = get_user(user_id)
    if is_premium(user):
        return True, 9999
    today = str(date.today())
    if user["last_date"] != today:
        return True, FREE_DAILY_LIMIT
    remaining = FREE_DAILY_LIMIT - user["uses"]
    return remaining > 0, max(remaining, 0)

# ── Gemini AI ─────────────────────────────────────────────────────────────────
async def ask_gemini(prompt):
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error("Gemini error: %s", e)
        return "❌ Error al generar respuesta. Intenta de nuevo."

# ── Menus ─────────────────────────────────────────────────────────────────────
def main_menu():
    keyboard = [
        [InlineKeyboardButton("📅 Calendario de Contenido", callback_data="calendario"),
         InlineKeyboardButton("✍️ Generador de Scripts", callback_data="scripts")],
        [InlineKeyboardButton("🔥 Cazador de Tendencias", callback_data="tendencias"),
         InlineKeyboardButton("#️⃣ Generador de Hashtags", callback_data="hashtags")],
        [InlineKeyboardButton("💬 Gestor de Comunidad", callback_data="comunidad"),
         InlineKeyboardButton("🎯 Analizador de Competencia", callback_data="competencia")],
        [InlineKeyboardButton("💰 Monetización Acelerada", callback_data="monetizacion"),
         InlineKeyboardButton("📧 Pitch a Marcas", callback_data="pitch")],
        [InlineKeyboardButton("🎨 Ideas Infinitas", callback_data="ideas"),
         InlineKeyboardButton("📊 Análisis de Crecimiento", callback_data="crecimiento")],
        [InlineKeyboardButton("🧠 Optimizador de Perfil", callback_data="perfil"),
         InlineKeyboardButton("🎙️ Coach en Cámara", callback_data="camara")],
        [InlineKeyboardButton("⭐ Premium — 299 Stars/mes", callback_data="buy_premium")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    referred_by = None
    if ctx.args:
        try:
            ref_id = int(ctx.args[0].replace("REF_", ""))
            if ref_id != uid:
                referred_by = ref_id
        except Exception:
            pass
    register_user(uid, referred_by)

    bot_username = (await ctx.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=REF_{uid}"
    user = get_user(uid)
    plan = "⭐ Pro (ilimitado)" if is_premium(user) else f"🆓 Gratis ({FREE_DAILY_LIMIT} usos/día)"

    text = (
        f"🎬 *¡Bienvenido a CreatorAI Bot!*\n\n"
        "Tu equipo creativo completo en Telegram. Te ayudo a crecer en TikTok, Instagram, YouTube y más.\n\n"
        f"📋 *Tu plan actual:* {plan}\n"
        f"⭐ *Pro:* ilimitado por solo *{STARS_PRICE} Stars/mes*\n\n"
        "💰 *¡Gana Stars invitando amigos!*\n"
        f"Por cada amigo que compre Pro ganas *{REFERRAL_COMMISSION} Stars*.\n"
        f"👉 Tu link: `{ref_link}`\n\n"
        "👇 *Elige un servicio:*"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu())

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    register_user(uid)
    await update.message.reply_text("👇 *Elige un servicio:*", parse_mode="Markdown", reply_markup=main_menu())

async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    register_user(uid)
    user = get_user(uid)
    today = str(date.today())
    used = user["uses"] if user["last_date"] == today else 0

    if is_premium(user):
        msg = (
            f"⭐ *Plan:* Pro (ilimitado)\n"
            f"📅 *Vence:* {user['premium_until']}\n\n"
            f"💰 Stars ganadas: *{user['stars_earned']} ⭐*\n"
            f"👥 Amigos referidos: *{user['referral_count']}*"
        )
    else:
        msg = (
            f"🆓 *Plan:* Gratis\n"
            f"📊 Usos hoy: *{used}/{FREE_DAILY_LIMIT}*\n\n"
            f"💰 Stars ganadas: *{user['stars_earned']} ⭐*\n"
            f"👥 Amigos referidos: *{user['referral_count']}*\n\n"
            f"¿Quieres ilimitado? /premium"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def referido_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    register_user(uid)
    user = get_user(uid)
    bot_username = (await ctx.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=REF_{uid}"
    msg = (
        "💰 *Tu link de referido:*\n\n"
        f"`{ref_link}`\n\n"
        f"Por cada amigo que compre Pro ganas *{REFERRAL_COMMISSION} Stars* automáticamente.\n\n"
        f"📊 *Tu historial:*\n"
        f"👥 Amigos referidos: *{user['referral_count']}*\n"
        f"⭐ Stars ganadas: *{user['stars_earned']}*\n\n"
        f"💡 Invita 5 amigos = *{5 * REFERRAL_COMMISSION} Stars* (~Premium gratis)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def premium_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("⭐ Comprar Pro — 299 Stars/mes", callback_data="buy_premium")]]
    await update.message.reply_text(
        f"⭐ *CreatorAI Pro — Todo ilimitado*\n\n"
        f"Precio: *{STARS_PRICE} Telegram Stars/mes* (~$3.90 USD)\n\n"
        "✅ Los 12 servicios sin límites\n"
        "✅ Respuestas personalizadas a tu nicho\n"
        "✅ Acceso a tendencias en tiempo real\n"
        "✅ Soporte prioritario\n\n"
        "💰 ¿Sin Stars? Usa /referido para ganarlas gratis.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def buy_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "buy_premium":
        await ctx.bot.send_invoice(
            chat_id=query.from_user.id,
            title="CreatorAI Bot Pro",
            description="Acceso ilimitado a los 12 servicios por 30 días",
            payload="pro_30d",
            currency="XTR",
            prices=[LabeledPrice("Pro 30 días", STARS_PRICE)],
            provider_token="",
        )
        return

    uid = query.from_user.id
    register_user(uid)
    allowed, remaining = can_use(uid)

    if not allowed:
        bot_username = (await ctx.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start=REF_{uid}"
        keyboard = [[InlineKeyboardButton("⭐ Comprar Pro — 299 Stars", callback_data="buy_premium")]]
        await query.message.reply_text(
            f"⛔ Has usado tus *{FREE_DAILY_LIMIT} usos gratuitos* de hoy.\n\n"
            "¿Cómo conseguir más?\n\n"
            "⭐ *Opción 1:* Compra Pro por solo *299 Stars*\n\n"
            f"💰 *Opción 2:* Comparte tu link y gana Stars gratis:\n`{ref_link}`\n"
            f"Cada amigo que compre = *{REFERRAL_COMMISSION} Stars para ti*\n\n"
            "🕐 *Opción 3:* Vuelve mañana",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    user = get_user(uid)
    service = query.data
    ctx.user_data["service"] = service
    ctx.user_data["nicho"] = user.get("nicho", "")
    ctx.user_data["red_social"] = user.get("red_social", "")
    ctx.user_data["tono"] = user.get("tono", "")

    prompts = {
        "calendario": "📅 *Calendario de Contenido*\n\nCuéntame:\n- ¿Cuál es tu nicho? (ej: fitness, cocina, viajes)\n- ¿En qué red social? (TikTok, Instagram, YouTube)\n- ¿Cuántos seguidores tienes aproximadamente?",
        "scripts": "✍️ *Generador de Scripts*\n\nCuéntame:\n- ¿Cuál es el tema del video?\n- ¿Tu tono? (serio, gracioso, educativo, motivacional)\n- ¿Para qué red social?",
        "tendencias": "🔥 *Cazador de Tendencias*\n\nCuéntame:\n- ¿Cuál es tu nicho?\n- ¿En qué red social publicas?",
        "hashtags": "#️⃣ *Generador de Hashtags*\n\nCuéntame:\n- ¿Cuál es el tema de tu publicación?\n- ¿Para qué red social? (TikTok, Instagram, YouTube)\n- ¿Tu público es hispanohablante, anglohablante o ambos?",
        "comunidad": "💬 *Gestor de Comunidad*\n\nPega aquí el comentario difícil, pregunta frecuente o mensaje de hater que necesitas responder:",
        "competencia": "🎯 *Analizador de Competencia*\n\nCuéntame:\n- ¿Cuál es el @ o nombre de tu competidor?\n- ¿En qué red social?\n- ¿Cuál es tu nicho?",
        "monetizacion": "💰 *Monetización Acelerada*\n\nCuéntame:\n- ¿Cuál es tu nicho?\n- ¿Cuántos seguidores tienes?\n- ¿En qué red social?",
        "pitch": "📧 *Pitch a Marcas*\n\nCuéntame:\n- ¿Cuál es tu nicho?\n- ¿Cuántos seguidores tienes?\n- ¿Qué tipo de marcas te interesan?",
        "ideas": "🎨 *Ideas de Contenido Infinitas*\n\nCuéntame:\n- ¿Cuál es tu nicho?\n- ¿Para qué red social?\n- ¿Qué tipo de ideas quieres? (virales, educativas, entretenimiento)",
        "crecimiento": "📊 *Análisis de Crecimiento*\n\nCuéntame tus métricas de la última semana:\n- Seguidores ganados/perdidos\n- Publicaciones subidas\n- Tu red social y nicho",
        "perfil": "🧠 *Optimizador de Perfil*\n\nCuéntame:\n- ¿Cuál es tu nicho?\n- ¿En qué red social?\n- Pega tu bio actual (o escribe 'no tengo' si es nueva)",
        "camara": "🎙️ *Coach de Presencia en Cámara*\n\nCuéntame:\n- ¿Cuál es tu nicho?\n- ¿Dónde grabas? (casa, exterior, oficina)\n- ¿Cuál es tu mayor dificultad frente a la cámara?",
    }

    await query.message.reply_text(prompts.get(service, "Cuéntame más detalles:"), parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    register_user(uid)
    text = update.message.text.strip()
    service = ctx.user_data.get("service")

    if not service:
        await update.message.reply_text(
            "👇 Usa el menú para elegir un servicio:",
            reply_markup=main_menu()
        )
        return

    allowed, remaining = can_use(uid)
    if not allowed:
        bot_username = (await ctx.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start=REF_{uid}"
        keyboard = [[InlineKeyboardButton("⭐ Comprar Pro — 299 Stars", callback_data="buy_premium")]]
        await update.message.reply_text(
            f"⛔ Has usado tus *{FREE_DAILY_LIMIT} usos gratuitos* de hoy.\n\n"
            f"💰 Gana Stars compartiendo tu link:\n`{ref_link}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    msg = await update.message.reply_text("⏳ Generando respuesta con IA... espera un momento.")

    prompts_ia = {
        "calendario": f"""Eres un experto en marketing de contenidos. El usuario te dice: "{text}"
Genera un calendario de contenido completo de 30 días con:
- Tema específico para cada día
- Mejor hora para publicar según su país/región
- Tipo de formato recomendado (video corto, carrusel, foto, live, story)
- Distribución inteligente para no repetir temas
- Hashtags principales para cada semana
Sé específico, práctico y usa emojis para que sea fácil de leer.""",

        "scripts": f"""Eres un experto en creación de contenido viral. El usuario quiere un script sobre: "{text}"
Genera DOS versiones del script:
VERSION CORTA (30 segundos):
- Gancho poderoso para los primeros 3 segundos
- Desarrollo rápido
- Llamada a la acción

VERSION LARGA (60 segundos):
- Gancho poderoso
- Desarrollo con ritmo y pausas indicadas [PAUSA]
- Llamada a la acción fuerte

Incluye indicaciones de tono, gestos y momentos clave. Sé específico y viral.""",

        "tendencias": f"""Eres un experto en tendencias de redes sociales. El usuario es creador de: "{text}"
Analiza y explica:
1. TENDENCIAS ACTUALES más relevantes para su nicho (audios, challenges, formatos)
2. Cómo adaptar cada tendencia a su contenido específico
3. Ventana de oportunidad — cuánto tiempo tiene cada tendencia
4. Qué tendencia aprovechar PRIMERO y por qué
5. Ideas concretas de videos usando estas tendencias
Sé específico con nombres de tendencias reales y actuales.""",

        "hashtags": f"""Eres un experto en SEO de redes sociales. El usuario necesita hashtags para: "{text}"
Genera una estrategia completa de hashtags:
HASHTAGS GRANDES (más de 1M publicaciones): 5 hashtags
HASHTAGS MEDIANOS (100K-1M): 10 hashtags  
HASHTAGS PEQUEÑOS (menos de 100K, más específicos): 10 hashtags
HASHTAGS EN INGLÉS (para llegar a más audiencia): 5 hashtags

Explica la estrategia y cómo combinarlos para máximo alcance.""",

        "comunidad": f"""Eres un experto en gestión de comunidades digitales. El usuario recibió este mensaje: "{text}"
Genera:
1. RESPUESTA PRINCIPAL — diplomática, que no pierde seguidores y genera engagement
2. RESPUESTA ALTERNATIVA — más directa y firme si es un hater
3. RESPUESTA CORTA — para cuando no tienes tiempo
4. Consejo sobre cómo manejar esta situación a largo plazo
Sé específico, natural y mantén el tono profesional.""",

        "competencia": f"""Eres un estratega de marketing digital. El usuario quiere analizar a: "{text}"
Genera un análisis completo:
1. ESTRATEGIA DE CONTENIDO probable basada en su nicho
2. FRECUENCIA DE PUBLICACIÓN recomendada para competir
3. HORARIOS ÓPTIMOS para publicar en ese nicho
4. HASHTAGS que probablemente usa
5. PUNTOS DÉBILES que puedes aprovechar
6. ESTRATEGIA DE DIFERENCIACIÓN — cómo ser mejor que él
7. OPORTUNIDADES que él no está aprovechando
Sé estratégico y accionable.""",

        "monetizacion": f"""Eres un experto en monetización de contenido digital. El usuario tiene: "{text}"
Genera una guía completa:
1. ESTADO ACTUAL — qué puede monetizar YA con lo que tiene
2. PLATAFORMAS — requisitos exactos para monetizar en cada red social
3. PRIMER SPONSOR — cómo conseguirlo aunque tenga pocos seguidores
4. MEDIA KIT — plantilla profesional lista para usar
5. PRECIOS RECOMENDADOS — tabla con rangos según tamaño de audiencia
6. PRODUCTO DIGITAL — idea específica para crear y vender
7. PLAN DE 90 DÍAS — pasos concretos para monetizar
Sé específico con números y estrategias reales.""",

        "pitch": f"""Eres un experto en relaciones con marcas. El usuario tiene: "{text}"
Genera:
1. EMAIL COMPLETO — listo para copiar y enviar a marcas
2. MARCAS ESPECÍFICAS — lista de 10 marcas que patrocinan ese nicho
3. ESTRATEGIA DE NEGOCIACIÓN — cómo pedir precio sin quedar mal
4. CONTRATO BÁSICO — cláusulas esenciales para protegerte
5. SEGUIMIENTO — qué hacer si no responden
Sé específico y profesional.""",

        "ideas": f"""Eres un director creativo de contenido viral. El usuario crea contenido de: "{text}"
Genera 15 ideas de contenido:
5 IDEAS VIRALES — alto potencial de viralización
5 IDEAS EDUCATIVAS — que posicionen como experto
5 IDEAS DE ENTRETENIMIENTO — que generen engagement

Para cada idea incluye:
- Título llamativo
- Concepto en 2 líneas
- Por qué funcionaría
- Gancho inicial sugerido
Sé creativo, específico y original.""",

        "crecimiento": f"""Eres un analista de growth para creadores de contenido. El usuario comparte: "{text}"
Genera un análisis completo:
1. DIAGNÓSTICO — por qué está creciendo o estancado
2. CONTENIDO GANADOR — qué tipo funciona mejor en su caso
3. ERRORES DETECTADOS — qué está haciendo mal
4. PLAN SEMANA SIGUIENTE — acciones concretas día por día
5. META REALISTA — predicción de crecimiento en 30/60/90 días
6. KPIs — métricas clave que debe monitorear
Sé honesto, específico y accionable.""",

        "perfil": f"""Eres un experto en optimización de perfiles digitales. El usuario tiene: "{text}"
Genera un análisis y mejora completa:
1. BIO OPTIMIZADA — versión mejorada lista para copiar
2. FOTO DE PERFIL — descripción exacta de qué tipo de foto usar
3. NOMBRE DE USUARIO — sugerencias optimizadas para búsquedas
4. LINK EN BIO — qué poner y cómo organizarlo (herramientas recomendadas)
5. HIGHLIGHTS — qué categorías crear en Instagram Stories
6. PALABRAS CLAVE — términos que debe incluir para el algoritmo
Sé específico y práctico.""",

        "camara": f"""Eres un coach de presencia en cámara para creadores de contenido. El usuario me dice: "{text}"
Genera una guía completa y personalizada:
1. ILUMINACIÓN — configuración exacta para su situación
2. ENCUADRE Y ÁNGULOS — los mejores para su tipo de contenido
3. CONFIANZA EN CÁMARA — ejercicios específicos para mejorar
4. VOZ Y DICCIÓN — técnicas para hablar con más impacto
5. OUTFIT — qué colores y estilos funcionan para su nicho
6. ERRORES COMUNES — los más frecuentes y cómo evitarlos
7. RUTINA DE CALENTAMIENTO — qué hacer antes de grabar
Sé práctico y motivador.""",
    }

    prompt = prompts_ia.get(service, f"Ayuda al usuario con su consulta sobre creación de contenido: {text}")
    response = await ask_gemini(prompt)

    record_use(uid)
    user = get_user(uid)
    today = str(date.today())
    used = user["uses"] if user["last_date"] == today else 1
    left = FREE_DAILY_LIMIT - used

    footer = ""
    if not is_premium(user):
        footer = f"\n\n━━━━━━━━━━━━━━━\n🎁 Usos gratuitos restantes hoy: *{max(left,0)}/{FREE_DAILY_LIMIT}*"
        if left <= 0:
            bot_username = (await ctx.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start=REF_{uid}"
            footer += f"\n⭐ ¿Quieres ilimitado? /premium\n💰 Gana Stars gratis: `{ref_link}`"

    await msg.edit_text(response + footer, parse_mode="Markdown", reply_markup=main_menu())
    ctx.user_data["service"] = None

async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload == "pro_30d":
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Pago no reconocido.")

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    until = str(date.today() + timedelta(days=30))
    set_premium(uid, until)

    user = get_user(uid)
    if user.get("referred_by"):
        referrer_id = user["referred_by"]
        add_commission(referrer_id)
        try:
            await ctx.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 *¡Ganaste {REFERRAL_COMMISSION} Stars!*\n\nUno de tus referidos compró Pro. ¡Sigue compartiendo tu link con /referido! 💰",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    await update.message.reply_text(
        f"🎉 *¡Bienvenido a CreatorAI Pro!*\n\n"
        f"✅ Acceso ilimitado a los 12 servicios\n"
        f"📅 Tu Pro vence el *{until}*\n\n"
        "¡Empieza a crear contenido increíble! 🚀",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("referido", referido_cmd))
    app.add_handler(CallbackQueryHandler(buy_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("CreatorAI Bot iniciado ✅")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
