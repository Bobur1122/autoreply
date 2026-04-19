# Telegram relay (bot + userbot)

Bu loyiha serverda **Telegram bot** sifatida ishlaydi va sozlamalarni Telegram ichidan (tugmalar/yozishmalar orqali) qabul qiladi. Keyin u **sizning Telegram akkauntingiz (userbot)** orqali manba kanal/guruhdan kelgan xabarlarni manzil kanal/guruhga yuboradi va xabardagi:

- linklar
- telefon raqamlar
- `@username`lar

ni siz bergan qiymatlar bilan almashtiradi.

## Muhim

- Source kanaldagi postlarni oddiy bot ko‘ra olmaydi (admin bo‘lmasa). Shu sabab relay **userbot** orqali qilinadi.
- Userbot sessiyasi (`StringSession`) maxfiy. Biz uni `DB_CHAT` ga **pinned** xabar qilib saqlaymiz. `DB_CHAT` faqat siz ko‘radigan private kanal/guruh bo‘lsin va botda **Pin messages** huquqi bo‘lsin.

## 1) Talablar

- Python 3.11+ (Render uchun `runtime.txt` bor)
- Bot token: `@BotFather`
- Telegram API ID/HASH: `https://my.telegram.org` (buni bot ichida ulash paytida yuborasiz)
- `DB_CHAT`: private kanal/guruh (bot admin bo‘lsin)

## 2) Lokal ishga tushirish

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\main.py
```

## 3) Render’ga qo‘yish

Render → Service → Environment Variables:

- `BOT_TOKEN`
- `DB_CHAT` (masalan `@my_private_db_channel` yoki `-100...`)
- `OWNER_ID` (tavsiya) — botga “🆔 Mening ID” yuboring va chiqqan id’ni qo‘ying
- `NON_INTERACTIVE=1` (tavsiya)

Ikki xil ishga tushirish bor:

- **Background Worker (polling)** — oddiy `getUpdates`. Faqat **bitta** instansiya ishlasin (aks holda `TelegramConflictError` chiqadi).
- **Web Service (webhook, tavsiya)** — Render `PORT` beradi va bot webhook orqali ishlaydi.
  - `WEBHOOK_BASE_URL=https://<your-service>.onrender.com` qo‘ying (yoki Render `RENDER_EXTERNAL_URL` bersa, o‘zi oladi)
  - Health: `/` → `ok`

Agar siz botni boshqa joyda ham ishga tushirgan bo‘lsangiz (kompyuterda / boshqa hostingda), o‘shani o‘chiring — aks holda Telegram bitta bot token bilan 2 ta `getUpdates`’ga ruxsat bermaydi.

## Telegram limit (FloodWait)

Agar log’da `FloodWaitError: A wait of N seconds is required` ko‘rsangiz, bu Render “uxlab qoldi” degani emas — bu Telegram userbot akkauntingizni vaqtinchalik limit qilgan bo‘ladi. Bot relay’ni avtomatik **N sekunddan keyin** qayta urunib ishga tushiradi.

## 4) Botni sozlash (Telegram ichida)

1) Botga `/start`
2) `/claim` (owner o‘rnatish)
3) “➕ Akkaunt ulash (userbot)”:
   - `TG_API_ID` (raqam) yuborasiz
   - `TG_API_HASH` (string) yuborasiz
   - ulash usulini tanlaysiz:
     - “🔳 QR orqali (tavsiya)” — linkni bosib Telegram’da tasdiqlaysiz, keyin “✅ Tekshirish”
     - “📱 Telefon/kod orqali” — telefon yuborasiz, kod yuborasiz (Telegram buni bloklashi mumkin)
4) “📥 Manba (source)” → `@kanal1,@kanal2` (yoki `-100...`)
5) “📤 Manzil (dest)” → `@dest` (yoki `-100...`)
6) “▶️ Ishga tushirish”

## Zip qilib yuborish

`.venv/`, `__pycache__/`, `*.session*`, `.env` ni zip’ga qo‘shmang.

```powershell
.\pack.ps1
```

Natija: `project.zip`.
