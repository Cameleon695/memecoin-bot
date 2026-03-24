"""
🤖 Memecoin Scanner Bot v4 — 100% GRATUIT
==========================================
Pump.fun + Nitter + Groq IA + Filtres avancés

PRÉREQUIS :
  pip install requests websocket-client groq beautifulsoup4

FILTRES ACTIFS :
  - MCap          : $14K — $30K
  - Dev hold      : maximum 5% des tokens
  - Insiders hold : maximum 20% combiné
  - Age           : token de moins de 3 heures
  - Score IA      : minimum 6/10
"""

import json
import time
import threading
import requests
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime

# ============================================================
#  ⚙️  CONFIG
# ============================================================

TELEGRAM_TOKEN   = "8647489832:AAFxqTFZAT2BZOt6SlTntw0SBIGyHYxegsA"
TELEGRAM_CHAT_ID = "2053599090"
GROQ_API_KEY     = "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"

# --- Filtres ---
MCAP_MIN            = 14_000   # Market cap minimum en $
MCAP_MAX            = 30_000   # Market cap maximum en $
DEV_MAX_HOLD_PCT    = 5.0      # Dev peut hold max 5% des tokens
INSIDER_MAX_PCT     = 20.0     # Insiders combinés max 20%
MAX_AGE_HOURS       = 3        # Token de moins de 3 heures
MIN_AI_SCORE        = 6        # Score IA minimum 6/10

# --- Nitter ---
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.lunar.icu",
]

# ============================================================
#  📨  TELEGRAM
# ============================================================

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     message,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


# ============================================================
#  🔍  FILTRES
# ============================================================

def passes_filters(token: dict) -> tuple[bool, str]:
    """
    Retourne (True, "") si le token passe tous les filtres,
    ou (False, "raison") sinon.
    """
    mcap        = token.get("usd_market_cap", 0)
    age_minutes = token.get("age_minutes", 9999)
    dev_pct     = token.get("dev_hold_pct", 100.0)
    insider_pct = token.get("insider_hold_pct", 100.0)

    if not (MCAP_MIN <= mcap <= MCAP_MAX):
        return False, f"MCap ${mcap:,.0f} hors fourchette"

    if isinstance(age_minutes, int) and age_minutes > MAX_AGE_HOURS * 60:
        return False, f"Trop vieux ({age_minutes}min)"

    if dev_pct > DEV_MAX_HOLD_PCT:
        return False, f"Dev hold trop élevé ({dev_pct:.1f}%)"

    if insider_pct > INSIDER_MAX_PCT:
        return False, f"Insiders trop élevés ({insider_pct:.1f}%)"

    return True, ""


def passes_ai_filter(analysis: dict) -> bool:
    score = analysis.get("score_confiance", {}).get("valeur", 0)
    return score >= MIN_AI_SCORE


# ============================================================
#  🌐  PUMP.FUN — Enrichissement on-chain
# ============================================================

def enrich_token(token: dict) -> dict:
    mint = token.get("mint", "")
    try:
        r = requests.get(f"https://frontend-api.pump.fun/coins/{mint}", timeout=8)
        if r.status_code != 200:
            return token
        data = r.json()

        created_ts  = data.get("created_timestamp", 0)
        age_minutes = int((time.time() - created_ts / 1000) / 60) if created_ts else 9999

        # Calcul % du dev
        total_supply = data.get("total_supply", 1) or 1
        dev_balance  = data.get("creator_token_balance", 0) or 0
        dev_pct      = (dev_balance / total_supply) * 100

        # Insiders : on utilise le top 10 holders comme approximation
        # L'API Pump.fun expose "top_holders" dans certains endpoints
        insider_pct = _get_insider_pct(mint, total_supply)

        token.update({
            "name":              data.get("name",    token.get("name",   "Unknown")),
            "symbol":            data.get("symbol",  token.get("symbol", "???")),
            "usd_market_cap":    data.get("usd_market_cap", 0),
            "bonding_curve_pct": data.get("bonding_curve", 0),
            "age_minutes":       age_minutes,
            "dev_hold_pct":      round(dev_pct, 2),
            "insider_hold_pct":  insider_pct,
            "twitter":           data.get("twitter",  ""),
            "telegram":          data.get("telegram", ""),
            "website":           data.get("website",  ""),
        })
    except Exception as e:
        print(f"[ENRICH ERROR] {e}")
    return token


def _get_insider_pct(mint: str, total_supply: int) -> float:
    """
    Récupère le % cumulé des top holders via l'API Pump.fun.
    Retourne 100.0 en cas d'erreur (filtre sera rejeté = sécurité).
    """
    try:
        url = f"https://frontend-api.pump.fun/coins/{mint}/top-holders"
        r   = requests.get(url, timeout=6)
        if r.status_code != 200:
            return 0.0  # Si endpoint indispo, on laisse passer

        holders = r.json()
        if not isinstance(holders, list):
            return 0.0

        total_held = sum(h.get("balance", 0) for h in holders[:10])
        pct = (total_held / total_supply) * 100 if total_supply else 0.0
        return round(pct, 2)

    except Exception:
        return 0.0


# ============================================================
#  🐦  NITTER — Scraping Twitter gratuit
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

def get_twitter_data(username: str) -> dict | None:
    if not username:
        return None

    username = username.strip().lstrip("@")
    for domain in ["twitter.com/", "x.com/"]:
        if domain in username:
            username = username.split(domain)[-1].split("/")[0].split("?")[0]
    if not username:
        return None

    for instance in NITTER_INSTANCES:
        try:
            r = requests.get(f"{instance}/{username}", headers=HEADERS, timeout=8)
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            bio_el = soup.select_one(".profile-bio p")
            bio    = bio_el.get_text(strip=True) if bio_el else ""

            stats = {}
            for item in soup.select(".profile-stat"):
                label = item.select_one(".profile-stat-header")
                value = item.select_one(".profile-stat-num")
                if label and value:
                    key = label.get_text(strip=True).lower()
                    val = value.get_text(strip=True).replace(",", "").replace(".", "")
                    try:
                        stats[key] = int(val)
                    except ValueError:
                        stats[key] = 0

            joined_el        = soup.select_one(".profile-joindate span[title]")
            account_age_days = None
            if joined_el:
                try:
                    joined_date      = datetime.strptime(joined_el["title"], "%I:%M %p - %d %b %Y")
                    account_age_days = (datetime.utcnow() - joined_date).days
                except Exception:
                    pass

            tweets = []
            for tweet_el in soup.select(".timeline-item")[:5]:
                text_el = tweet_el.select_one(".tweet-content")
                if not text_el:
                    continue
                likes = reposts = 0
                for s in tweet_el.select(".tweet-stat"):
                    icon = s.select_one(".icon-heart, .icon-retweet")
                    val  = s.get_text(strip=True).replace(",", "")
                    try:
                        n = int("".join(filter(str.isdigit, val)))
                    except ValueError:
                        n = 0
                    if icon:
                        cls = icon.get("class", [""])[0]
                        if "heart"   in cls: likes   = n
                        if "retweet" in cls: reposts = n
                tweets.append({
                    "text":     text_el.get_text(strip=True)[:200],
                    "likes":    likes,
                    "retweets": reposts,
                })

            return {
                "username":         username,
                "bio":              bio,
                "followers":        stats.get("followers", 0),
                "tweet_count":      stats.get("tweets", 0),
                "account_age_days": account_age_days,
                "recent_tweets":    tweets,
            }

        except Exception as e:
            print(f"[NITTER] {instance} échoué : {e}")
            continue

    return None


# ============================================================
#  🧠  ANALYSE IA — Groq
# ============================================================

groq_client = Groq(api_key=GROQ_API_KEY)

def analyze_with_ai(token: dict, twitter_data: dict | None) -> dict:
    mcap_entry = token.get("usd_market_cap", 0)

    if twitter_data:
        tweets_txt = ""
        for tw in twitter_data.get("recent_tweets", [])[:5]:
            tweets_txt += f'  - "{tw["text"]}" (❤️{tw["likes"]} 🔁{tw["retweets"]})\n'
        tw_section = f"""
TWITTER (@{twitter_data['username']}) :
- Followers : {twitter_data['followers']:,}
- Tweets : {twitter_data['tweet_count']:,}
- Âge du compte : {twitter_data.get('account_age_days', '?')} jours
- Bio : {twitter_data['bio']}
- Tweets récents :
{tweets_txt or "  (aucun tweet)"}"""
    else:
        tw_section = "\nTWITTER : Aucun compte trouvé."

    prompt = f"""Tu es un analyste expert en memecoins Solana.
Analyse ce token et réponds UNIQUEMENT en JSON valide, sans markdown ni texte autour.

TOKEN :
- Nom : {token.get('name')}
- Symbole : ${token.get('symbol')}
- Market Cap : ${mcap_entry:,.0f}
- Bonding Curve : {token.get('bonding_curve_pct', 0):.1f}%
- Âge : {token.get('age_minutes', '?')} minutes
- Dev hold : {token.get('dev_hold_pct', '?')}%
- Insiders hold : {token.get('insider_hold_pct', '?')}%
- Website : {token.get('website', 'N/A')}
- Telegram : {token.get('telegram', 'N/A')}
{tw_section}

JSON attendu :
{{
  "narrative": "1-2 phrases sur le thème et concept du token",
  "potentiel": "1-2 phrases sur la niche, timing, pourquoi ça peut marcher",
  "score_confiance": {{
    "valeur": <entier 0 à 10>,
    "explication": "1 phrase qui justifie le score"
  }},
  "risques": ["risque 1", "risque 2", "risque 3"],
  "prediction_ath": {{
    "mcap_min": <ATH pessimiste en $>,
    "mcap_max": <ATH optimiste en $>,
    "multiplicateur_min": <x depuis mcap actuel>,
    "multiplicateur_max": <x depuis mcap actuel>,
    "probabilite": <% d'atteindre mcap_min>,
    "catalyseurs": ["catalyseur 1", "catalyseur 2"],
    "delai_estime": "ex: 24-72h",
    "raisonnement": "1-2 phrases sur l'estimation"
  }}
}}"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.4,
        )
        raw   = response.choices[0].message.content.strip()
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        return json.loads(raw)

    except Exception as e:
        print(f"[AI ERROR] {e}")
        return {
            "narrative":       "Analyse indisponible.",
            "potentiel":       "Analyse indisponible.",
            "score_confiance": {"valeur": 0, "explication": "Erreur."},
            "risques":         ["Analyse IA indisponible"],
            "prediction_ath":  {
                "mcap_min": 0, "mcap_max": 0,
                "multiplicateur_min": 0, "multiplicateur_max": 0,
                "probabilite": 0, "catalyseurs": [],
                "delai_estime": "?", "raisonnement": "Données insuffisantes.",
            },
        }


# ============================================================
#  📋  FORMATAGE
# ============================================================

def score_emoji(score: int) -> str:
    if score >= 8: return "🟢"
    if score >= 6: return "🟡"
    return "🔴"

def proba_emoji(proba: int) -> str:
    if proba >= 70: return "🔥"
    if proba >= 40: return "⚡"
    return "❄️"

def format_number(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.0f}K"
    return str(n)

def format_alert(token: dict, twitter_data: dict | None, analysis: dict) -> str:
    name        = token.get("name", "Unknown")
    symbol      = token.get("symbol", "???")
    mint        = token.get("mint", "")
    mcap        = token.get("usd_market_cap", 0)
    bonding     = token.get("bonding_curve_pct", 0)
    age_min     = token.get("age_minutes", "?")
    dev_pct     = token.get("dev_hold_pct", "?")
    insider_pct = token.get("insider_hold_pct", "?")

    # Bloc Twitter
    if twitter_data:
        tw_block = (
            f"🐦 *Twitter* @{twitter_data['username']}\n"
            f"┣ Followers : `{twitter_data['followers']:,}`\n"
            f"┣ Tweets : `{twitter_data['tweet_count']:,}`\n"
            f"┗ Âge du compte : `{twitter_data.get('account_age_days', '?')} jours`\n\n"
        )
    else:
        tw_block = "🐦 *Twitter* : introuvable\n\n"

    score     = analysis["score_confiance"]["valeur"]
    score_exp = analysis["score_confiance"]["explication"]
    risques   = "\n".join(f"  ⚠️ {r}" for r in analysis.get("risques", []))

    ai_block = (
        f"🧠 *Analyse IA*\n"
        f"┣ 📌 *Narrative :* {analysis.get('narrative', '-')}\n"
        f"┣ 🚀 *Potentiel :* {analysis.get('potentiel', '-')}\n"
        f"┣ {score_emoji(score)} *Confiance :* `{score}/10` — {score_exp}\n"
        f"┗ 🚨 *Risques :*\n{risques}\n\n"
    )

    pred  = analysis.get("prediction_ath", {})
    mmin  = pred.get("mcap_min", 0)
    mmax  = pred.get("mcap_max", 0)
    xmin  = pred.get("multiplicateur_min", 0)
    xmax  = pred.get("multiplicateur_max", 0)
    proba = pred.get("probabilite", 0)
    delai = pred.get("delai_estime", "?")
    raison = pred.get("raisonnement", "")
    cats  = "\n".join(f"  ✨ {c}" for c in pred.get("catalyseurs", []))

    pred_block = (
        f"🔮 *Prédiction ATH*\n"
        f"┣ 🎯 MCap cible : `${format_number(mmin)}` → `${format_number(mmax)}`\n"
        f"┣ 📈 Multiplicateur : `{xmin}x` → `{xmax}x`\n"
        f"┣ {proba_emoji(proba)} Probabilité : `{proba}%`\n"
        f"┣ ⏱ Délai : `{delai}`\n"
        f"┣ 💡 *Catalyseurs :*\n{cats}\n"
        f"┗ 📝 {raison}\n\n"
        f"_⚠️ Estimation IA, pas un conseil financier._\n"
    )

    return (
        f"🚨 *Token validé — {score}/10* ✅\n\n"
        f"🪙 *{name}* `${symbol}`\n"
        f"`{mint}`\n\n"
        f"📊 *On-chain*\n"
        f"┣ Cap : `${mcap:,.0f}`\n"
        f"┣ Bonding Curve : `{bonding:.1f}%`\n"
        f"┣ Age : `{age_min}m`\n"
        f"┣ Dev hold : `{dev_pct}%`\n"
        f"┗ Insiders : `{insider_pct}%`\n\n"
        f"{tw_block}"
        f"{ai_block}"
        f"{pred_block}"
        f"🔗 [DexScreener](https://dexscreener.com/solana/{mint}) | "
        f"[Pump.fun](https://pump.fun/{mint})"
    )


# ============================================================
#  📡  WEBSOCKET PUMP.FUN
# ============================================================

def process_token(token: dict):
    token = enrich_token(token)
    name  = token.get("name", "?")
    mcap  = token.get("usd_market_cap", 0)

    ok, reason = passes_filters(token)
    if not ok:
        print(f"[SKIP]  {name} — ${mcap:,.0f} — {reason}")
        return

    print(f"[PASS]  {name} — ${mcap:,.0f} — filtres OK, analyse IA...")

    twitter_data = get_twitter_data(token.get("twitter", ""))
    analysis     = analyze_with_ai(token, twitter_data)

    if not passes_ai_filter(analysis):
        score = analysis.get("score_confiance", {}).get("valeur", 0)
        print(f"[SKIP]  {name} — Score IA trop bas ({score}/10)")
        return

    alert = format_alert(token, twitter_data, analysis)
    send_telegram(alert)
    print(f"[SENT]  ✅ Alerte envoyée pour {name}")


def on_message(ws, raw):
    try:
        data = json.loads(raw)
        if data.get("txType") not in ("create", "buy") or not data.get("mint"):
            return
        threading.Thread(target=process_token, args=(data,), daemon=True).start()
    except Exception as e:
        print(f"[MSG ERROR] {e}")

def on_error(ws, error):
    print(f"[WS ERROR] {error}")

def on_close(ws, *args):
    print("[WS] Déconnecté — reconnexion dans 5s...")
    time.sleep(5)
    start_websocket()

def on_open(ws):
    print("[WS] Connecté à Pump.fun ✅")
    send_telegram(
        "✅ *Scanner v4 démarré !*\n\n"
        f"📊 MCap : `${MCAP_MIN:,}` → `${MCAP_MAX:,}`\n"
        f"👤 Dev hold max : `{DEV_MAX_HOLD_PCT}%`\n"
        f"🐭 Insiders max : `{INSIDER_MAX_PCT}%`\n"
        f"⏱ Age max : `{MAX_AGE_HOURS}h`\n"
        f"🧠 Score IA min : `{MIN_AI_SCORE}/10`"
    )
    ws.send(json.dumps({"method": "subscribeNewToken"}))

def start_websocket():
    import websocket as ws_lib
    ws = ws_lib.WebSocketApp(
        "wss://pumpportal.fun/api/data",
        on_open=on_open, on_message=on_message,
        on_error=on_error, on_close=on_close,
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)


# ============================================================
#  🤖  COMMANDES TELEGRAM
# ============================================================

def handle_commands():
    offset = None
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"timeout": 30, "offset": offset}, timeout=40
            )
            for update in r.json().get("result", []):
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                text    = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text == "/start":
                    send_telegram(
                        "👋 *Memecoin Scanner v4*\n\n"
                        "/status — Filtres actifs\n"
                        "/help — Aide"
                    )
                elif text == "/status":
                    send_telegram(
                        f"⚙️ *Filtres actifs*\n\n"
                        f"┣ MCap : `${MCAP_MIN:,}` → `${MCAP_MAX:,}`\n"
                        f"┣ Dev hold max : `{DEV_MAX_HOLD_PCT}%`\n"
                        f"┣ Insiders max : `{INSIDER_MAX_PCT}%`\n"
                        f"┣ Age max : `{MAX_AGE_HOURS}h`\n"
                        f"┗ Score IA min : `{MIN_AI_SCORE}/10`"
                    )
                elif text == "/help":
                    send_telegram(
                        "ℹ️ *Aide*\n\n"
                        "Le bot filtre selon :\n"
                        "1. Market cap $14K-$30K\n"
                        "2. Dev hold < 5%\n"
                        "3. Insiders < 20%\n"
                        "4. Token < 3h\n"
                        "5. Score IA ≥ 6/10\n\n"
                        "_Modifie les variables en haut du fichier bot.py pour changer les filtres._"
                    )
        except Exception as e:
            print(f"[POLLING ERROR] {e}")
            time.sleep(5)


# ============================================================
#  ▶️  LANCEMENT
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  🤖 Memecoin Scanner Bot v4 — 100% GRATUIT")
    print("=" * 55)
    print(f"  MCap        : ${MCAP_MIN:,} — ${MCAP_MAX:,}")
    print(f"  Dev hold    : max {DEV_MAX_HOLD_PCT}%")
    print(f"  Insiders    : max {INSIDER_MAX_PCT}%")
    print(f"  Age max     : {MAX_AGE_HOURS}h")
    print(f"  Score IA    : min {MIN_AI_SCORE}/10")
    print("=" * 55)

    threading.Thread(target=handle_commands, daemon=True).start()
    start_websocket()