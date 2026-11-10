import unittest
import core


class FakeTG:
    def __init__(self):
        self.sent = []
        self.answers = []
        self.edits = []
        self.fail = False

    def send_message(self, chat_id, text, markup=None):
        if self.fail:
            raise core.TelegramError("blocked")
        self.sent.append((chat_id, text, markup))

    def answer_callback(self, cb_id, text=""):
        self.answers.append(text)

    def edit_message(self, chat_id, mid, text):
        self.edits.append(text)


def make():
    tg = FakeTG()
    return core.TwoFA(core.MemoryStore(), tg, "demo_bot", "Test"), tg


def link(tf, uid="ali", chat=111):
    t = tf.create_link(uid)["link_token"]
    tf.handle_update({"message": {"chat": {"id": chat, "type": "private"}, "text": "/start " + t}})


def last_code(tg):
    return tg.sent[-1][1].split("<code>")[1].split("</code>")[0]


class T(unittest.TestCase):
    def test_link_flow(self):
        tf, tg = make()
        self.assertFalse(tf.is_linked("ali"))
        d = tf.create_link("ali")
        self.assertIn("t.me/demo_bot?start=", d["deep_link"])
        link(tf)
        self.assertTrue(tf.is_linked("ali"))
        self.assertIn("ulandi", tg.sent[-1][1])

    def test_link_token_single_use_and_bad(self):
        tf, tg = make()
        t = tf.create_link("ali")["link_token"]
        self.assertEqual(tf.link_chat(1, t), "ali")
        self.assertIsNone(tf.link_chat(2, t))
        self.assertIsNone(tf.link_chat(2, "yoq"))

    def test_code_ok(self):
        tf, tg = make()
        link(tf)
        tf.send_code("ali")
        code = last_code(tg)
        self.assertEqual(len(code), 6)
        self.assertEqual(tf.verify_code("ali", "000000" if code != "000000" else "111111")["reason"], "wrong_code")
        self.assertTrue(tf.verify_code("ali", code)["valid"])
        self.assertEqual(tf.verify_code("ali", code)["reason"], "expired")  # bir martalik

    def test_attempt_limit(self):
        tf, tg = make()
        link(tf)
        tf.send_code("ali")
        code = last_code(tg)
        bad = "999999" if code != "999999" else "888888"
        for _ in range(5):
            self.assertEqual(tf.verify_code("ali", bad)["reason"], "wrong_code")
        self.assertEqual(tf.verify_code("ali", bad)["reason"], "too_many_attempts")
        self.assertEqual(tf.verify_code("ali", code)["reason"], "expired")

    def test_send_rate_limit(self):
        tf, tg = make()
        link(tf)
        for _ in range(core.MAX_SENDS):
            tf.send_code("ali")
        with self.assertRaises(core.TwoFAError) as c:
            tf.send_code("ali")
        self.assertEqual(c.exception.status, 429)

    def test_not_linked_and_bad_uid(self):
        tf, _ = make()
        with self.assertRaises(core.TwoFAError) as c:
            tf.send_code("ali")
        self.assertEqual(c.exception.status, 409)
        with self.assertRaises(core.TwoFAError) as c:
            tf.send_code("a b/../")
        self.assertEqual(c.exception.status, 400)

    def test_telegram_failure_removes_code(self):
        tf, tg = make()
        link(tf)
        tg.fail = True
        with self.assertRaises(core.TwoFAError) as c:
            tf.send_code("ali")
        self.assertEqual(c.exception.status, 502)
        self.assertEqual(tf.verify_code("ali", "123456")["reason"], "expired")

    def test_approval(self):
        tf, tg = make()
        link(tf)
        rid = tf.request_approval("ali")["request_id"]
        self.assertEqual(tf.approval_status(rid)["status"], "pending")
        cb = {"id": "1", "data": "a:" + rid, "message": {"chat": {"id": 111}, "message_id": 5}}
        tf.handle_update({"callback_query": cb})
        self.assertEqual(tf.approval_status(rid)["status"], "approved")
        tf.handle_update({"callback_query": dict(cb, data="d:" + rid)})  # qayta bosish
        self.assertEqual(tf.approval_status(rid)["status"], "approved")

    def test_approval_deny_and_foreign_chat(self):
        tf, tg = make()
        link(tf)
        rid = tf.request_approval("ali")["request_id"]
        foreign = {"id": "1", "data": "a:" + rid, "message": {"chat": {"id": 999}, "message_id": 5}}
        tf.handle_update({"callback_query": foreign})
        self.assertEqual(tf.approval_status(rid)["status"], "pending")
        own = {"id": "2", "data": "d:" + rid, "message": {"chat": {"id": 111}, "message_id": 5}}
        tf.handle_update({"callback_query": own})
        self.assertEqual(tf.approval_status(rid)["status"], "denied")

    def test_unknown_status_and_junk(self):
        tf, _ = make()
        self.assertEqual(tf.approval_status("xxx")["status"], "expired")
        self.assertEqual(tf.approval_status("../../")["status"], "expired")
        tf.handle_update({})
        tf.handle_update({"callback_query": {"id": "1", "data": "zzz"}})

    def test_welcome_has_site_and_help(self):
        tg = FakeTG()
        tf = core.TwoFA(core.MemoryStore(), tg, "demo_bot", "Test", "https://sayt.uz")
        for txt in ("/start", "/help", "salom"):
            tf.handle_update({"message": {"chat": {"id": 5, "type": "private"}, "text": txt}})
            self.assertIn("https://sayt.uz", tg.sent[-1][1])
            self.assertIn("Qanday ishlatiladi", tg.sent[-1][1])
        tf2, tg2 = make()
        tf2.handle_update({"message": {"chat": {"id": 5, "type": "private"}, "text": "/start"}})
        self.assertNotIn("Sayt:", tg2.sent[-1][1])

    def test_expiry(self):
        s = core.MemoryStore()
        s.set("k", "v", 0.05)
        import time
        time.sleep(0.08)
        self.assertIsNone(s.get("k"))
        self.assertEqual(s.incr("c", 60), 1)
        self.assertEqual(s.incr("c", 60), 2)


if __name__ == "__main__":
    unittest.main()
