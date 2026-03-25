"""
🤖 Memecoin Scanner Bot v5 — Scanner uniquement
=================================================
Scan les tokens récents Pump.fun toutes les 2 minutes.

PRÉREQUIS :
  pip install requests groq beautifulsoup4
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
MCAP_MIN         = 10_000
MCAP_MAX         = 40_000
DEV_MAX_HOLD_PCT = 10.0
INSIDER_MAX_PCT  = 20.0
MAX_AGE_HOURS    = 4
MIN_AI_SCORE     = 4

# --- Scanner ---
SCAN_INTERVAL_SEC = 120   # toutes les 2 minutes
SCAN_LIMIT        = 200    # 50 tokens récents par scan

# Anti-doublon
already_alerted      = set()
already_alerted_lock = threading.Lock()

# ============================================================
#  📨  TELEGRAM
# ============================================================

def send_telegram(message: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     message,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10
        ).raise_for_status()
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


# ============================================================
#  🔍  FILTRES
# ============================================================

def passes_filters(token: dict) -> tuple[bool, str]:
    mcap        = token.get("usd_market_cap", 0)
    age_minutes = token.get("age_minutes", 9999)
    dev_pct     = token.get("dev_hold_pct", 100.0)
    insider_pct = token.get("insider_hold_pct", 100.0)

    if not (MCAP_MIN <= mcap <= MCAP_MAX):
        return False, f"MCap ${mcap:,.0f} hors fourchette"
    if isinstance(age_minutes, int) and age_minutes > MAX_AGE_HOURS * 60:
        return False, f"Trop vieux ({age_minutes}min)"
    if dev_pct > DEV_MAX_HOLD_PCT:
        return False, f"Dev hold {dev_pct:.1f}% > {DEV_MAX_HOLD_PCT}%"
    if insider_pct > INSIDER_MAX_PCT:
        return False, f"Insiders {insider_pct:.1f}% > {INSIDER_MAX_PCT}%"
    return True, ""


# ============================================================
#  🌐  PUMP.FUN — Enrichissement
# ============================================================

def enrich_token(mint: str, base: dict = {}) -> dict:
    try:
        r    = requests.get(f"https://frontend-api.pump.fun/coins/{mint}", timeout=8)
        data = r.json() if r.status_code == 200 else {}

        created_ts   = data.get("created_timestamp", 0)
        age_minutes  = int((time.time() - created_ts / 1000) / 60) if created_ts else 9999
        total_supply = data.get("total_supply", 1) or 1
        dev_balance  = data.get("creator_token_balance", 0) or 0
        dev_pct      = round((dev_balance / total_supply) * 100, 2)
        insider_pct  = _get_insider_pct(mint, total_supply)

        return {
            "mint":              mint,
            "name":              data.get("name",    base.get("name",   "Unknown")),
            "symbol":            data.get("symbol",  base.get("symbol", "???")),
            "usd_market_cap":    data.get("usd_market_cap", 0),
            "bonding_curve_pct": data.get("bonding_curve", 0),
            "age_minutes":       age_minutes,
            "dev_hold_pct":      dev_pct,
            "insider_hold_pct":  insider_pct,
            "twitter":           data.get("twitter",  ""),
            "telegram":          data.get("telegram", ""),
            "website":           data.get("website",  ""),
        }
    except Exception as e:
        print(f"[ENRICH ERROR] {e}")
        return base


def _get_insider_pct(mint: str, total_supply: int) -> float:
    try:
        r       = requests.get(f"https://frontend-api.pump.fun/coins/{mint}/top-holders", timeout=6)
        holders = r.json() if r.status_code == 200 else []
        total   = sum(h.get("balance", 0) for h in holders[:10])
        return round((total / total_supply) * 100, 2) if total_supply else 0.0
    except Exception:
        return 0.0


# ============================================================
#  🐦  NITTER
# ============================================================

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.lunar.icu",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

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
            r    = requests.get(f"{instance}/{username}", headers=HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            bio_el = soup.select_one(".profile-bio p")
            stats  = {}
            for item in soup.select(".profile-stat"):
                label = item.select_one(".profile-stat-header")
                value = item.select_one(".profile-stat-num")
                if label and value:
                    try:
                        stats[label.get_text(strip=True).lower()] = int(
                            value.get_text(strip=True).replace(",", "").replace(".", "")
                        )
                    except ValueError:
                        pass

            joined_el        = soup.select_one(".profile-joindate span[title]")
            account_age_days = None
            if joined_el:
                try:
                    account_age_days = (
                        datetime.utcnow() - datetime.strptime(joined_el["title"], "%I:%M %p - %d %b %Y")
                    ).days
                except Exception:
                    pass

            tweets = []
            for tw in soup.select(".timeline-item")[:5]:
                txt = tw.select_one(".tweet-content")
                if txt:
                    tweets.append({"text": txt.get_text(strip=True)[:200], "likes": 0, "retweets": 0})

            return {
                "username":         username,
                "bio":              bio_el.get_text(strip=True) if bio_el else "",
                "followers":        stats.get("followers", 0),
                "tweet_count":      stats.get("tweets", 0),
                "account_age_days": account_age_days,
                "recent_tweets":    tweets,
            }
        except Exception:
            continue
    return None


# ============================================================
#  🧠  GROQ IA
# ============================================================

groq_client = Groq(api_key=GROQ_API_KEY)

def analyze_with_ai(token: dict, twitter_data: dict | None) -> dict:
    mcap = token.get("usd_market_cap", 0)

    tw_section = "\nTWITTER : Aucun compte trouvé."
    if twitter_data:
        tweets_txt = "".join(
            f'  - "{tw["text"]}" (❤️{tw["likes"]} 🔁{tw["retweets"]})\n'
            for tw in twitter_data.get("recent_tweets", [])[:5]
        )
        tw_section = f"""
TWITTER (@{twitter_data['username']}) :
- Followers : {twitter_data['followers']:,}
- Tweets : {twitter_data['tweet_count']:,}
- Âge : {twitter_data.get('account_age_days', '?')} jours
- Bio : {twitter_data['bio']}
- Tweets récents :
{tweets_txt or "  (aucun tweet)"}"""

    prompt = f"""Tu es un analyste expert en memecoins Solana.
Réponds UNIQUEMENT en JSON valide, sans markdown ni texte autour.

TOKEN :
- Nom : {token.get('name')} / ${token.get('symbol')}
- MCap : ${mcap:,.0f}
- Bonding Curve : {token.get('bonding_curve_pct', 0):.1f}%
- Âge : {token.get('age_minutes', '?')} minutes
- Dev hold : {token.get('dev_hold_pct', '?')}%
- Insiders : {token.get('insider_hold_pct', '?')}%
- Website : {token.get('website', 'N/A')}
- Telegram : {token.get('telegram', 'N/A')}
{tw_section}

JSON :
{{
  "narrative": "1-2 phrases sur le thème/concept",
  "potentiel": "1-2 phrases sur la niche et le timing",
  "score_confiance": {{"valeur": <0-10>, "explication": "1 phrase"}},
  "risques": ["risque 1", "risque 2", "risque 3"],
  "prediction_ath": {{
    "mcap_min": <$>, "mcap_max": <$>,
    "multiplicateur_min": <x>, "multiplicateur_max": <x>,
    "probabilite": <%>,
    "catalyseurs": ["cat 1", "cat 2"],
    "delai_estime": "ex: 24-72h",
    "raisonnement": "1-2 phrases"
  }}
}}"""

    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700, temperature=0.4,
        )
        raw   = resp.choices[0].message.content.strip()
        start = raw.find("{"); end = raw.rfind("}") + 1
        return json.loads(raw[start:end]) if start != -1 and end > start else _default_analysis()
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return _default_analysis()


def _default_analysis():
    return {
        "narrative": "Analyse indisponible.", "potentiel": "Analyse indisponible.",
        "score_confiance": {"valeur": 0, "explication": "Erreur."},
        "risques": ["Analyse IA indisponible"],
        "prediction_ath": {
            "mcap_min": 0, "mcap_max": 0, "multiplicateur_min": 0, "multiplicateur_max": 0,
            "probabilite": 0, "catalyseurs": [], "delai_estime": "?", "raisonnement": "Données insuffisantes.",
        },
    }


# ============================================================
#  📋  FORMATAGE
# ============================================================

def fmt(n): return f"{n/1_000_000:.1f}M" if n >= 1_000_000 else f"{n/1_000:.0f}K" if n >= 1_000 else str(n)
def semoji(s): return "🟢" if s >= 8 else "🟡" if s >= 6 else "🔴"
def pemoji(p): return "🔥" if p >= 70 else "⚡" if p >= 40 else "❄️"

def format_alert(token: dict, twitter_data: dict | None, analysis: dict) -> str:
    score = analysis["score_confiance"]["valeur"]
    pred  = analysis.get("prediction_ath", {})
    cats  = "\n".join(f"  ✨ {c}" for c in pred.get("catalyseurs", []))
    risks = "\n".join(f"  ⚠️ {r}" for r in analysis.get("risques", []))

    tw_block = (
        f"🐦 *Twitter* @{twitter_data['username']}\n"
        f"┣ Followers : `{twitter_data['followers']:,}`\n"
        f"┗ Âge du compte : `{twitter_data.get('account_age_days','?')} jours`\n\n"
    ) if twitter_data else "🐦 *Twitter* : introuvable\n\n"

    mint = token.get('mint', '')
    return (
        f"🚨 *Token validé — {score}/10* 🔄\n\n"
        f"🪙 *{token.get('name')}* `${token.get('symbol')}`\n"
        f"`{mint}`\n\n"
        f"📊 *On-chain*\n"
        f"┣ Cap : `${token.get('usd_market_cap',0):,.0f}`\n"
        f"┣ Bonding Curve : `{token.get('bonding_curve_pct',0):.1f}%`\n"
        f"┣ Age : `{token.get('age_minutes','?')}m`\n"
        f"┣ Dev hold : `{token.get('dev_hold_pct','?')}%`\n"
        f"┗ Insiders : `{token.get('insider_hold_pct','?')}%`\n\n"
        f"{tw_block}"
        f"🧠 *Analyse IA*\n"
        f"┣ 📌 {analysis.get('narrative','-')}\n"
        f"┣ 🚀 {analysis.get('potentiel','-')}\n"
        f"┣ {semoji(score)} *Confiance :* `{score}/10` — {analysis['score_confiance']['explication']}\n"
        f"┗ 🚨 *Risques :*\n{risks}\n\n"
        f"🔮 *Prédiction ATH*\n"
        f"┣ 🎯 `${fmt(pred.get('mcap_min',0))}` → `${fmt(pred.get('mcap_max',0))}`\n"
        f"┣ 📈 `{pred.get('multiplicateur_min',0)}x` → `{pred.get('multiplicateur_max',0)}x`\n"
        f"┣ {pemoji(pred.get('probabilite',0))} Proba : `{pred.get('probabilite',0)}%`\n"
        f"┣ ⏱ `{pred.get('delai_estime','?')}`\n"
        f"┣ 💡 *Catalyseurs :*\n{cats}\n"
        f"┗ 📝 {pred.get('raisonnement','')}\n\n"
        f"_⚠️ Estimation IA, pas un conseil financier._\n\n"
        f"🔗 [DexScreener](https://dexscreener.com/solana/{mint}) | [Pump.fun](https://pump.fun/{mint})"
    )


# ============================================================
#  🔄  SCANNER PRINCIPAL
# ============================================================

def scan_loop():
    print(f"[SCANNER] Démarré — scan toutes les {SCAN_INTERVAL_SEC}s")
    send_telegram(
        "✅ *Scanner v5 démarré !*\n\n"
        f"📊 MCap : `${MCAP_MIN:,}` → `${MCAP_MAX:,}`\n"
        f"👤 Dev hold max : `{DEV_MAX_HOLD_PCT}%`\n"
        f"🐭 Insiders max : `{INSIDER_MAX_PCT}%`\n"
        f"⏱ Age max : `{MAX_AGE_HOURS}h`\n"
        f"🧠 Score IA min : `{MIN_AI_SCORE}/10`\n"
        f"🔄 Scan toutes les : `{SCAN_INTERVAL_SEC}s`"
    )

    while True:
        try:
            r      = requests.get(
                "https://frontend-api.pump.fun/coins",
                params={"offset": 0, "limit": SCAN_LIMIT, "sort": "created_timestamp", "order": "DESC"},
                timeout=10
            )
            tokens = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
            print(f"[SCANNER] {len(tokens)} tokens — analyse...")

            for data in tokens:
                mint = data.get("mint", "")
                if not mint:
                    continue

                # Anti-doublon
                with already_alerted_lock:
                    if mint in already_alerted:
                        continue

                # Pré-filtre rapide
                mcap       = data.get("usd_market_cap", 0)
                created_ts = data.get("created_timestamp", 0)
                age_min    = int((time.time() - created_ts / 1000) / 60) if created_ts else 9999

                if not (MCAP_MIN <= mcap <= MCAP_MAX):
                    continue
                if age_min > MAX_AGE_HOURS * 60:
                    continue

                # Enrichissement complet
                token = enrich_token(mint, {"name": data.get("name"), "symbol": data.get("symbol")})
                ok, reason = passes_filters(token)
                if not ok:
                    print(f"[SKIP] {token.get('name')} — {reason}")
                    continue

                print(f"[PASS] {token.get('name')} — ${mcap:,.0f} — IA en cours...")

                # Analyse IA
                twitter_data = get_twitter_data(token.get("twitter", ""))
                analysis     = analyze_with_ai(token, twitter_data)
                score        = analysis.get("score_confiance", {}).get("valeur", 0)

                if score < MIN_AI_SCORE:
                    print(f"[SKIP AI] {token.get('name')} — Score {score}/10 trop bas")
                    continue

                # Alerte !
                with already_alerted_lock:
                    already_alerted.add(mint)

                send_telegram(format_alert(token, twitter_data, analysis))
                print(f"[SENT] ✅ {token.get('name')} — Score {score}/10")

        except Exception as e:
            print(f"[SCANNER ERROR] {e}")

        print(f"[SCANNER] Prochain scan dans {SCAN_INTERVAL_SEC}s...")
        time.sleep(SCAN_INTERVAL_SEC)


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
                    send_telegram("👋 *Scanner v5 actif !*\n/status — Filtres\n/help — Aide")
                elif text == "/status":
                    send_telegram(
                        f"⚙️ *Filtres actifs*\n\n"
                        f"┣ MCap : `${MCAP_MIN:,}` → `${MCAP_MAX:,}`\n"
                        f"┣ Dev hold max : `{DEV_MAX_HOLD_PCT}%`\n"
                        f"┣ Insiders max : `{INSIDER_MAX_PCT}%`\n"
                        f"┣ Age max : `{MAX_AGE_HOURS}h`\n"
                        f"┣ Score IA min : `{MIN_AI_SCORE}/10`\n"
                        f"┗ Scan : toutes les `{SCAN_INTERVAL_SEC}s`"
                    )
                elif text == "/help":
                    send_telegram(
                        "ℹ️ *Aide*\n\n"
                        "Le bot scanne les 50 tokens récents toutes les 2 minutes "
                        "et t'alerte si tous les critères sont remplis.\n\n"
                        "Modifie les variables en haut du fichier `bot.py` pour changer les filtres."
                    )
        except Exception as e:
            print(f"[POLLING ERROR] {e}")
            time.sleep(5)


# ============================================================
#  ▶️  LANCEMENT
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  🤖 Memecoin Scanner Bot v5 — Scanner uniquement")
    print("=" * 55)
    print(f"  MCap     : ${MCAP_MIN:,} — ${MCAP_MAX:,}")
    print(f"  Dev hold : max {DEV_MAX_HOLD_PCT}%")
    print(f"  Insiders : max {INSIDER_MAX_PCT}%")
    print(f"  Age max  : {MAX_AGE_HOURS}h")
    print(f"  Score IA : min {MIN_AI_SCORE}/10")
    print(f"  Scan     : toutes les {SCAN_INTERVAL_SEC}s")
    print("=" * 55)

    threading.Thread(target=handle_commands, daemon=True).start()
    scan_loop()
