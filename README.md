# Telegram orqali 2FA moduli

Veb-sayt uchun ikki bosqichli autentifikatsiya: foydalanuvchiga Telegram bot orqali **bir martalik kod** yoki **✅/❌ tasdiqlash tugmasi** yuboriladi.

## 1. Bot yaratish (2 daqiqa)
1. Telegramda **@BotFather** ni oching → `/newbot`.
2. Bot nomi va username kiriting (oxiri `bot` bilan tugashi kerak).
3. BotFather bergan **tokenni** nusxalang.

## 2. Ishga tushirish
```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# .env ichiga BOT_TOKEN va API_KEY ni yozing
uvicorn main:app
```
Brauzerda oching: http://127.0.0.1:8000 (demo sahifa).
Sinab ko'rish tartibi: nom kiriting → «Telegramda ochish» → START bosing → «Kod yuborish» yoki «Tugma bilan tasdiqlash».

> Ishga tushirishda faqat **bitta** jarayon ishlatilsin (`--workers` ishlatmang).
> Bot xuddi shu serverda "long polling" bilan ishlaydi, shuning uchun domen yoki webhook shart emas.

## 3. Boshqa sayt API orqali ulansa
Barcha so'rovlarga `X-API-Key: <API_KEY>` sarlavhasi kerak. Misol:

| Metod va yo'l | Vazifa |
|---|---|
| `POST /api/link` `{"user_id":"ali"}` | Ulash havolasi (`deep_link`) |
| `GET /api/link/ali` | Telegram ulanganmi (`linked`) |
| `POST /api/send` `{"user_id":"ali"}` | Kod yuborish |
| `POST /api/verify` `{"user_id":"ali","code":"123456"}` | Kodni tekshirish → `valid: true/false` |
| `POST /api/approve` `{"user_id":"ali"}` | Tasdiqlash so'rovi → `request_id` |
| `GET /api/approve/{request_id}` | `pending` / `approved` / `denied` / `expired` |

```bash
curl -X POST http://127.0.0.1:8000/api/send -H "X-API-Key: KALIT" -H "Content-Type: application/json" -d '{"user_id":"ali"}'
```
Swagger hujjat: http://127.0.0.1:8000/docs

## Xavfsizlik qoidalari (kodda bor)
- Kod 6 xonali, `secrets` bilan yaratiladi, 5 daqiqa amal qiladi va faqat **bir marta** ishlaydi.
- Bitta kodga 5 ta urinish. 10 daqiqada 5 tadan ko'p kod yuborib bo'lmaydi.
- Tasdiqlash tugmasini faqat hisob ulangan Telegram chatdan bosish mumkin.
- Demo sahifada `API_KEY` brauzerga chiqmaydi. Haqiqiy ishda `.env` da `DEMO_MODE=0` qiling.

## Redis
Odatda shart emas. Ishlab chiqarishda `.env` ga `REDIS_URL=redis://localhost:6379/0` yozsangiz, ma'lumotlar Redisda saqlanadi va server qayta ishga tushsa ham yo'qolmaydi.

## Testlar
`python test_core.py` (Telegram va internet kerak emas).

## Fayllar
- `core.py` asosiy mantiq va bot
- `main.py` FastAPI server (API + demo)
- `static/index.html` demo sahifa
- `test_core.py` avtomatik testlar

## Internetga chiqarish (Render, bepul)
1. Loyihani GitHub'ga yuklang (`.env` faylini YUKLAMANG, token faqat Render sozlamalarida turadi).
2. render.com → New → Web Service → GitHub repozitoriyni tanlang.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Environment: `BOT_TOKEN` (kerak bo'lsa `SERVICE_NAME`). `SITE_URL` shart emas, Render manzili avtomatik olinadi.
6. Bepul tarifda sayt 15 daqiqa harakatsiz tursa uxlaydi va bot ham javob bermay qoladi. `https://<sayt>/health` manzilini UptimeRobot kabi xizmat bilan 5 daqiqada bir tekshirtirib qo'ying.
7. Bir tokenni faqat bitta joyda ishlating (kompyuterda ham, Renderda ham bir vaqtda ishlatmang).
