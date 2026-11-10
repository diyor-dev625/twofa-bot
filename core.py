"""2FA moduli: asosiy mantiq. Faqat standart kutubxonalar (Redis ixtiyoriy)."""
import html
import json
import logging
import os
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
import hmac

log = logging.getLogger("twofa")

CODE_TTL = 300        # kod amal qilish muddati (soniya)
LINK_TTL = 600        # ulash havolasi muddati
APPROVE_TTL = 300     # "tasdiqlash" so'rovi muddati
MAX_ATTEMPTS = 5      # bitta kod uchun urinishlar soni
MAX_SENDS = 5         # SEND_WINDOW ichida nechta kod yuborish mumkin
SEND_WINDOW = 600

UID_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def load_env(path=".env"):
    """.env faylini o'qiydi (python-dotenv shart emas)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class TwoFAError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class TelegramError(Exception):
    pass


# ---------------- Saqlash joyi ----------------
class MemoryStore:
    """Redis bo'lmasa ishlatiladi (server qayta ishga tushsa ma'lumot o'chadi)."""

    def __init__(self):
        self.d = {}
        self.lock = threading.Lock()

    def _alive(self, k):
        v = self.d.get(k)
        if v is None:
            return None
        if v[1] is not None and v[1] <= time.time():
            del self.d[k]
            return None
        return v

    def get(self, k):
        with self.lock:
            v = self._alive(k)
            return v[0] if v else None

    def set(self, k, val, ttl=None):
        with self.lock:
            self.d[k] = (str(val), time.time() + ttl if ttl else None)

    def delete(self, k):
        with self.lock:
            self.d.pop(k, None)

    def incr(self, k, ttl):
        with self.lock:
            v = self._alive(k)
            if v:
                n = int(v[0]) + 1
                self.d[k] = (str(n), v[1])
            else:
                n = 1
                self.d[k] = ("1", time.time() + ttl)
            return n


class RedisStore:
    def __init__(self, url):
        import redis
        self.r = redis.Redis.from_url(url, decode_responses=True)
        self.r.ping()

    def get(self, k):
        return self.r.get(k)

    def set(self, k, val, ttl=None):
        if ttl:
            self.r.set(k, str(val), ex=ttl)
        else:
            self.r.set(k, str(val))

    def delete(self, k):
        self.r.delete(k)

    def incr(self, k, ttl):
        n = self.r.incr(k)
        if self.r.ttl(k) < 0:
            self.r.expire(k, ttl)
        return n


# ---------------- Telegram ----------------
class Telegram:
    def __init__(self, token):
        self.base = "https://api.telegram.org/bot%s/" % token
        self.ctx = ssl.create_default_context()
        if os.getenv("TG_INSECURE_SSL", "0") == "1":
            # Faqat sinov uchun: tarmoq HTTPS ni to'sib/almashtirayotgan bo'lsa
            log.warning("TG_INSECURE_SSL=1: SSL sertifikat tekshiruvi o'chirilgan!")
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def call(self, method, payload=None, timeout=15):
        req = urllib.request.Request(
            self.base + method,
            data=json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                desc = json.loads(e.read()).get("description", "")
            except Exception:
                desc = ""
            raise TelegramError(desc or "HTTP %s" % e.code)
        except Exception as e:
            raise TelegramError(str(e))
        if not body.get("ok"):
            raise TelegramError(body.get("description", "Telegram xatosi"))
        return body["result"]

    def send_message(self, chat_id, text, markup=None):
        p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
             "disable_web_page_preview": True}
        if markup:
            p["reply_markup"] = markup
        return self.call("sendMessage", p)

    def edit_message(self, chat_id, message_id, text):
        return self.call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": text, "parse_mode": "HTML"})

    def answer_callback(self, cb_id, text=""):
        return self.call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


# ---------------- 2FA mantiqi ----------------
class TwoFA:
    def __init__(self, store, tg, bot_username, service="Sayt", site_url=""):
        self.store = store
        self.tg = tg
        self.bot_username = bot_username
        self.service = service
        self.site_url = site_url

    def welcome_text(self):
        site = ("\n🌐 Sayt: %s\n" % html.escape(self.site_url)) if self.site_url else ""
        return (
            "👋 Salom! Men <b>%s</b> uchun ikki bosqichli tasdiqlash (2FA) botiman.\n"
            "%s\n"
            "<b>Qanday ishlatiladi:</b>\n"
            "1. Saytni oching va foydalanuvchi nomini kiriting.\n"
            "2. «Ulash havolasini olish» → «Telegramda ochish» → START bosing. Hisobingiz ulanadi.\n"
            "3. Kirishda sayt sizga 6 xonali kod yoki ✅/❌ tugmali xabar yuboradi.\n"
            "4. Kodni saytga kiriting yoki ✅ tugmasini bosing.\n\n"
            "🔒 Kodni hech kimga bermang. Kod %d daqiqa amal qiladi.\n\n"
            "Buyruqlar: /start, /help"
            % (html.escape(self.service), site, CODE_TTL // 60)
        )

    @staticmethod
    def _uid(user_id):
        if not isinstance(user_id, str) or not UID_RE.match(user_id):
            raise TwoFAError(400, "user_id noto'g'ri (faqat harf, raqam va _ . @ - , 1-64 belgi)")
        return user_id

    def _chat(self, uid):
        chat = self.store.get("chat:" + uid)
        if not chat:
            raise TwoFAError(409, "Foydalanuvchi Telegramni ulamagan")
        return chat

    # --- hisobni ulash ---
    def create_link(self, user_id):
        uid = self._uid(user_id)
        token = secrets.token_urlsafe(16)
        self.store.set("link:" + token, uid, LINK_TTL)
        return {
            "link_token": token,
            "deep_link": "https://t.me/%s?start=%s" % (self.bot_username, token),
            "expires_in": LINK_TTL,
        }

    def link_chat(self, chat_id, token):
        uid = self.store.get("link:" + token)
        if not uid:
            return None
        self.store.delete("link:" + token)
        self.store.set("chat:" + uid, chat_id)
        return uid

    def is_linked(self, user_id):
        return self.store.get("chat:" + self._uid(user_id)) is not None

    # --- bir martalik kod ---
    def send_code(self, user_id):
        uid = self._uid(user_id)
        chat = self._chat(uid)
        if self.store.incr("rl:send:" + uid, SEND_WINDOW) > MAX_SENDS:
            raise TwoFAError(429, "Juda ko'p so'rov. Birozdan keyin urinib ko'ring")
        code = "%06d" % secrets.randbelow(10 ** 6)
        self.store.set("code:" + uid, code, CODE_TTL)
        self.store.delete("att:" + uid)
        text = ("🔐 <b>%s</b>\nTasdiqlash kodi: <code>%s</code>\n"
                "Kod %d daqiqa amal qiladi. Uni hech kimga bermang."
                % (html.escape(self.service), code, CODE_TTL // 60))
        try:
            self.tg.send_message(chat, text)
        except TelegramError as e:
            self.store.delete("code:" + uid)
            log.warning("Kod yuborilmadi: %s", e)
            raise TwoFAError(502, "Telegramga yuborib bo'lmadi")
        return {"sent": True, "expires_in": CODE_TTL}

    def verify_code(self, user_id, code):
        uid = self._uid(user_id)
        stored = self.store.get("code:" + uid)
        if not stored:
            return {"valid": False, "reason": "expired"}
        if self.store.incr("att:" + uid, CODE_TTL) > MAX_ATTEMPTS:
            self.store.delete("code:" + uid)
            return {"valid": False, "reason": "too_many_attempts"}
        if hmac.compare_digest(stored.encode(), str(code).strip().encode()):
            self.store.delete("code:" + uid)
            self.store.delete("att:" + uid)
            return {"valid": True}
        return {"valid": False, "reason": "wrong_code"}

    # --- tugma bilan tasdiqlash ---
    def request_approval(self, user_id):
        uid = self._uid(user_id)
        chat = self._chat(uid)
        if self.store.incr("rl:send:" + uid, SEND_WINDOW) > MAX_SENDS:
            raise TwoFAError(429, "Juda ko'p so'rov. Birozdan keyin urinib ko'ring")
        rid = secrets.token_urlsafe(8)
        self.store.set("req:" + rid, json.dumps({"uid": uid, "status": "pending"}), APPROVE_TTL)
        markup = {"inline_keyboard": [[
            {"text": "✅ Tasdiqlash", "callback_data": "a:" + rid},
            {"text": "❌ Rad etish", "callback_data": "d:" + rid},
        ]]}
        text = ("🔐 <b>%s</b>\nHisobingizga kirishga urinish bo'ldi. Bu siz edingizmi?"
                % html.escape(self.service))
        try:
            self.tg.send_message(chat, text, markup)
        except TelegramError as e:
            self.store.delete("req:" + rid)
            log.warning("So'rov yuborilmadi: %s", e)
            raise TwoFAError(502, "Telegramga yuborib bo'lmadi")
        return {"request_id": rid, "expires_in": APPROVE_TTL}

    def approval_status(self, rid):
        raw = self.store.get("req:" + rid) if re.match(r"^[A-Za-z0-9_-]{1,32}$", rid or "") else None
        if not raw:
            return {"status": "expired"}
        return {"status": json.loads(raw)["status"]}

    # --- Telegramdan kelgan yangilanishlar ---
    def handle_update(self, u):
        if "callback_query" in u:
            return self._on_callback(u["callback_query"])
        m = u.get("message")
        if not m or "text" not in m or m.get("chat", {}).get("type") != "private":
            return
        chat_id = m["chat"]["id"]
        text = m["text"].strip()
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                uid = self.link_chat(chat_id, parts[1].strip())
                if uid:
                    self.tg.send_message(chat_id, "✅ Hisob ulandi: <b>%s</b>\nEndi tasdiqlash kodlari shu yerga keladi." % html.escape(uid))
                else:
                    self.tg.send_message(chat_id, "❌ Havola eskirgan yoki noto'g'ri. Saytdan yangi havola oling.")
            else:
                self.tg.send_message(chat_id, self.welcome_text())
        else:
            self.tg.send_message(chat_id, self.welcome_text())

    def _on_callback(self, cb):
        cb_id = cb.get("id")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        mid = msg.get("message_id")
        data = cb.get("data", "")
        if ":" not in data:
            return self.tg.answer_callback(cb_id)
        action, rid = data.split(":", 1)
        raw = self.store.get("req:" + rid)
        if not raw or action not in ("a", "d"):
            self.tg.answer_callback(cb_id, "So'rov muddati tugagan")
            if chat_id and mid:
                self.tg.edit_message(chat_id, mid, "⌛ So'rov muddati tugagan.")
            return
        req = json.loads(raw)
        if str(chat_id) != str(self.store.get("chat:" + req["uid"])):
            return self.tg.answer_callback(cb_id, "Ruxsat yo'q")
        if req["status"] != "pending":
            return self.tg.answer_callback(cb_id, "Allaqachon javob berilgan")
        req["status"] = "approved" if action == "a" else "denied"
        self.store.set("req:" + rid, json.dumps(req), APPROVE_TTL)
        self.tg.answer_callback(cb_id, "Qabul qilindi")
        self.tg.edit_message(chat_id, mid, "✅ Tasdiqlandi." if action == "a" else "❌ Rad etildi.")


def poll_forever(tf, stop):
    """Telegramdan yangilanishlarni (long polling) olib turadi."""
    try:
        tf.tg.call("deleteWebhook")
    except TelegramError as e:
        log.warning("deleteWebhook: %s", e)
    offset = None
    while not stop.is_set():
        payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        try:
            updates = tf.tg.call("getUpdates", payload, timeout=40)
        except TelegramError as e:
            log.warning("getUpdates: %s", e)
            stop.wait(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                tf.handle_update(u)
            except Exception:
                log.exception("Yangilanishni qayta ishlashda xato")


def build(token):
    url = os.getenv("REDIS_URL", "").strip()
    store = RedisStore(url) if url else MemoryStore()
    tg = Telegram(token)
    try:
        me = tg.call("getMe")
    except TelegramError as e:
        raise RuntimeError("BOT_TOKEN noto'g'ri yoki Telegramga ulanib bo'lmadi: %s" % e)
    return TwoFA(store, tg, me["username"], os.getenv("SERVICE_NAME", "Demo sayt"),
                 (os.getenv("SITE_URL") or os.getenv("RENDER_EXTERNAL_URL")
                 or "http://127.0.0.1:8000").strip())
