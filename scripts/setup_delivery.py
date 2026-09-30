# -*- coding: utf-8 -*-
"""Өтінімді жеткізу арналарын баптау шебері.

Не үшін: жеткізу арнасы .env файлындағы кілтпен қосылады. Кілтті қолмен
тауып, файлдың дұрыс жеріне жазып, сосын жұмыс істеп тұрғанын тексеру —
бірнеше қадам. Бұл скрипт соның бәрін бір терезеде жасайды: сұрайды,
жазады, БІРДЕН тексереді.

Қауіпсіздік: кілтті ӨЗІҢІЗ өз терминалыңызға тересіз. Ол тек .env
файлына жазылады, ал .env — .gitignore ішінде, GitHub-қа шықпайды.

Іске қосу:
    python scripts/setup_delivery.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# ============================================================
#  .env файлымен жұмыс
# ============================================================

def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(updates: dict[str, str]) -> None:
    """Кілттерді .env ішіне жазу — түсініктемелер мен реті сақталады."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    left = dict(updates)

    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in left:
            lines[i] = f"{key}={left.pop(key)}"

    if left:
        lines.append("")
        lines.append("# --- Жеткізу арналары (setup_delivery.py қосты) ---")
        lines += [f"{key}={value}" for key, value in left.items()]

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Ағымдағы процесс те бірден көрсін — тексеру сол сәтте жүреді
    import os
    os.environ.update(updates)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Тоқтатылды.")
        sys.exit(0)
    return value or default


def valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", value))


# ============================================================
#  Ағымдағы күй
# ============================================================

def show_status() -> None:
    from portal import delivery

    print()
    print("  ═══ Жеткізу арналарының күйі ═══")
    state = delivery.health()
    labels = {"ekc109": "ЕКЦ 109", "email": "Email",
              "whatsapp": "WhatsApp", "telegram": "Telegram"}
    for key, info in state.items():
        mark = "  ҚОСУЛЫ " if info.get("configured") else "  өшірулі"
        extra = ""
        if key == "email" and info.get("configured"):
            extra = f" · {info.get('transport')} · {info.get('to')}"
        elif key == "whatsapp":
            extra = "  (сілтеме әрқашан жұмыс істейді, кілтсіз)"
        print(f"  {mark}  {labels[key]:9}{extra}")
    print()


# ============================================================
#  Арналарды баптау
# ============================================================

def setup_brevo() -> None:
    print("""
  ═══ BREVO — email (ҰСЫНЫЛАДЫ) ═══

  Gmail-дың «қосымша құпиясөзі» шықпаса, дұрысы осы: Brevo-ға
  қосымша құпиясөз де, домен де, екі сатылы кіру де қажет емес.
  Vercel-де де жұмыс істейді (Gmail SMTP онда жиі бұғатталады).

  Қадамдар:
    1. https://www.brevo.com  →  тіркелу (тегін, 300 хат/күн)
    2. Оң жақ жоғарыдағы атыңыз → Senders, Domains & Dedicated IPs
       → Senders → Add a sender → өз поштаңызды қосып, РАСТАҢЫЗ
       (поштаңызға растау хаты келеді)
    3. Атыңыз → SMTP & API → API Keys → Generate a new API key
       → атын AIQYN деп қойып, кілтті КӨШІРІҢІЗ
""")
    key = ask("Brevo API кілті (xkeysib-...)")
    if not key:
        print("  Кілт енгізілмеді — тоқтатылды.\n")
        return

    sender = ask("Жіберуші пошта (Brevo-да РАСТАЛҒАН)")
    if not valid_email(sender):
        print("  Пошта дұрыс емес — тоқтатылды.\n")
        return

    to = ask("Өтінім КІМГЕ барады (демо кезінде өз поштаңыз)", sender)
    if not valid_email(to):
        print("  Пошта дұрыс емес — тоқтатылды.\n")
        return

    write_env({
        "AIQYN_MAIL_PROVIDER": "brevo",
        "AIQYN_BREVO_KEY": key,
        "AIQYN_MAIL_FROM": sender,
        "AIQYN_DELIVERY_TO": to,
    })
    print("\n  .env жаңарды. Тексеріп көрейік...")
    send_test()


def setup_gmail() -> None:
    print("""
  ═══ GMAIL SMTP ═══

  Тек «қосымша құпиясөзбен» (app password) жұмыс істейді — өз
  құпиясөзіңізбен ЕМЕС.

    1. Екі сатылы кіру ҚОСУЛЫ болуы керек:
       https://myaccount.google.com/signinoptions/twosv
    2. https://myaccount.google.com/apppasswords
       → атын AIQYN деп қойыңыз → 16 таңбалы код

  Бет «қолжетімді емес» десе: не 2FA қосылмаған, не бұл мектеп/жұмыс
  аккаунты (әкімші өшірген). Ондай жағдайда Brevo нұсқасын таңдаңыз.

  ЕСКЕРТУ: Vercel-де 587-порт жиі жабық. Интернеттегі сайт үшін Brevo.
""")
    user = ask("Gmail мекенжайыңыз")
    if not valid_email(user):
        print("  Пошта дұрыс емес — тоқтатылды.\n")
        return

    password = ask("16 таңбалы қосымша құпиясөз").replace(" ", "")
    if len(password) < 12:
        print("  Бұл қосымша құпиясөзге ұқсамайды (16 таңба болуы керек).\n")
        return

    to = ask("Өтінім КІМГЕ барады (демо кезінде өз поштаңыз)", user)
    if not valid_email(to):
        print("  Пошта дұрыс емес — тоқтатылды.\n")
        return

    write_env({
        "AIQYN_MAIL_PROVIDER": "smtp",
        "AIQYN_SMTP_HOST": "smtp.gmail.com",
        "AIQYN_SMTP_PORT": "587",
        "AIQYN_SMTP_USER": user,
        "AIQYN_SMTP_PASSWORD": password,
        "AIQYN_SMTP_FROM": user,
        "AIQYN_DELIVERY_TO": to,
    })
    print("\n  .env жаңарды. Тексеріп көрейік...")
    send_test()


def setup_telegram() -> None:
    print("""
  ═══ TELEGRAM — ең жылдам арна (1 минут) ═══

  Қосымша құпиясөз де, 2FA да, тіркелу де қажет емес.

    1. Telegram-нан @BotFather-ды тауып, /newbot жазыңыз
    2. Ботқа ат беріңіз → ол ТОКЕН береді, көшіріңіз
    3. Өз ботыңызды тауып, оған кез келген хабар жазыңыз
    4. Браузерден ашыңыз:
       https://api.telegram.org/bot<ТОКЕН>/getUpdates
       Ішінен  "chat":{"id":123456789  деген санды алыңыз
""")
    token = ask("Бот токені")
    chat = ask("Chat ID (сан)")
    if not token or not chat:
        print("  Толық енгізілмеді — тоқтатылды.\n")
        return

    write_env({"AIQYN_TELEGRAM_TOKEN": token,
               "AIQYN_TELEGRAM_CHAT_ID": chat,
               "AIQYN_TELEGRAM_MIN_SEVERITY": "low"})

    from portal.notify import notifier
    ok, detail = notifier.check()
    print(f"\n  {'Бот жұмыс істейді: ' + detail if ok else 'Қате: ' + detail}\n")
    if ok:
        send_test()


def setup_whatsapp() -> None:
    print("""
  ═══ WHATSAPP ═══

  Кілтсіз жұмыс істейді: жүйе әр өтінімге дайын мәтінмен сілтеме
  жасайды, оператор бір басып жібереді — хабарлама шынымен жетеді.
  Мұнда тек НӨМІРДІ қою керек.
""")
    phone = ask("Қабылдайтын нөмір (+7 707 123 45 67)")
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        print("  Нөмір қысқа — тоқтатылды.\n")
        return
    write_env({"AIQYN_DELIVERY_WHATSAPP": "+" + digits})
    print(f"\n  Қойылды: +{digits}")
    print("  Порталда өтінімді растағанда WhatsApp сілтемесі шығады.\n")


def setup_safety() -> None:
    print("""
  ═══ ДЕМО ҚАУІПСІЗДІГІ ═══

  Бұл айнымалы БАРЛЫҚ өтінімді бір поштаға бұрады — аймақтың өз
  мекенжайы болса да, елемейді. Сахнада қате мекемеге хат кетіп
  қалмауының кепілі. Бос қалдырсаңыз — өшіріледі.
""")
    value = ask("Барлық хат баратын пошта (бос = өшіру)")
    if value and not valid_email(value):
        print("  Пошта дұрыс емес — тоқтатылды.\n")
        return
    write_env({"AIQYN_DELIVERY_FORCE_TO": value})
    print("  Қойылды.\n" if value else "  Өшірілді.\n")


# ============================================================
#  Сынақ
# ============================================================

def send_test() -> None:
    from portal import delivery

    print("  Сынақ хабары жіберілуде...\n")
    sample = {
        "event_id": "AIQYN-SETUP-TEST",
        "defect_type_official": "СЫНАҚ — байланыс арнасын тексеру",
        "defect_type_official_ru": "ТЕСТ — проверка канала связи",
        "address_text": "Шымкент қаласы (сынақ жазбасы)",
        "severity": "low",
        "timestamp_human": "—",
        "description_text": "Бұл — AIQYN ZHOL жүйесінің байланыс арнасын "
                            "тексеруге арналған СЫНАҚ хабары. Нақты жол ақауы "
                            "туралы өтінім емес, әрекет қажет етпейді.",
        "description_text_ru": "Это ТЕСТОВОЕ сообщение для проверки канала связи "
                               "системы AIQYN ZHOL. Не является заявкой.",
        "responsible_org": "Сынақ",
    }
    receipt = delivery.send(sample, ROOT / "portal" / "demo" / "media", "SETUP-TEST")

    labels = {"ekc109": "ЕКЦ 109", "email": "Email",
              "whatsapp": "WhatsApp", "telegram": "Telegram"}
    for key, channel in receipt["channels"].items():
        if channel.get("ok"):
            mark = "  ЖЕТТІ  "
        elif channel.get("configured"):
            mark = "  ҚАТЕ   "
        else:
            mark = "  —      "
        print(f"{mark}{labels[key]:9} {channel.get('detail', '')}")

    print()
    if "email" in receipt["delivered"]:
        print("  Пошта жәшігіңізді ашыңыз — «[AIQYN] ... СЫНАҚ ...» деген хат")
        print("  келуі керек. Келмесе, «Спам» бумасын да қараңыз.\n")
    elif receipt["channels"]["email"].get("configured"):
        print("  Хат жіберілмеді. Жоғарыдағы қате мәтінін оқыңыз:")
        print("    · «535» немесе «Username and Password not accepted»")
        print("      → қосымша құпиясөз дұрыс емес немесе кәдімгі құпиясөз")
        print("    · «unauthorized» / «401» → Brevo кілті дұрыс емес")
        print("    · «sender not valid» → Brevo-да жіберуші расталмаған\n")


def show_vercel_hint() -> None:
    values = read_env()
    keys = [k for k in ("AIQYN_MAIL_PROVIDER", "AIQYN_BREVO_KEY", "AIQYN_MAIL_FROM",
                        "AIQYN_DELIVERY_TO", "AIQYN_DELIVERY_FORCE_TO",
                        "AIQYN_DELIVERY_WHATSAPP", "AIQYN_TELEGRAM_TOKEN",
                        "AIQYN_TELEGRAM_CHAT_ID", "AIQYN_PORTAL_PUBLIC_URL")
            if values.get(k)]
    if not keys:
        print("  Әзірге бапталған кілт жоқ.\n")
        return
    print("""
  ═══ VERCEL-ГЕ КӨШІРУ ═══

  Интернеттегі сайт .env файлын КӨРМЕЙДІ — оның айнымалылары бөлек
  тұрады. Мына командаларды бірінен соң бірін орындаңыз (әрқайсысы
  мәнді сұрайды):
""")
    for key in keys:
        print(f"    vercel env add {key} production")
    print("""
  Сосын міндетті түрде:

    vercel --prod

  Vercel жаңа айнымалыларды тек қайта жинағанда ғана алады.
""")


MENU = """
  ╔══════════════════════════════════════════════════════╗
  ║  AIQYN — өтінімді жеткізуді баптау                   ║
  ╚══════════════════════════════════════════════════════╝

    1  Brevo — email  (ҰСЫНЫЛАДЫ: қосымша құпиясөз қажет емес)
    2  Gmail SMTP — email  (16 таңбалы қосымша құпиясөз керек)
    3  Telegram  (ең жылдамы, 1 минут)
    4  WhatsApp нөмірі  (кілтсіз жұмыс істейді)
    5  Демо қауіпсіздігі — барлық хатты бір поштаға бұру
    6  Сынақ хабарын жіберу
    7  Vercel-ге көшіру командалары
    0  Шығу
"""


def main() -> None:
    if not ENV_PATH.exists():
        example = ROOT / ".env.example"
        if example.exists():
            ENV_PATH.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            print("  .env файлы .env.example негізінде жасалды.")

    from vision.env import load_env
    load_env()

    actions = {"1": setup_brevo, "2": setup_gmail, "3": setup_telegram,
               "4": setup_whatsapp, "5": setup_safety, "6": send_test,
               "7": show_vercel_hint}

    while True:
        show_status()
        print(MENU)
        choice = ask("Таңдаңыз")
        if choice in ("0", "q", ""):
            print("  Сау болыңыз.\n")
            return
        action = actions.get(choice)
        if action is None:
            print("  Мұндай нұсқа жоқ.\n")
            continue
        try:
            action()
        except Exception as exc:                  # noqa: BLE001
            print(f"\n  Қате: {exc}\n")


if __name__ == "__main__":
    main()
