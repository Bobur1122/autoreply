# Telegram relay (userbot)

Bu loyiha **sizning Telegram akkauntingiz** orqali (`userbot`) bitta kanaldan kelgan xabarlarni boshqa kanalga yuboradi va xabardagi:

- linklar
- telefon raqamlar
- `@username` lar

ni siz bergan qiymatlar bilan almashtiradi.

## 1) Talablar

- Python 3.10+
- Telegram API ID va API HASH: `https://my.telegram.org`
- Source kanalda o‘qish huquqi, destination kanalda yozish huquqi (admin bo‘lishingiz mumkin)

## 2) O‘rnatish

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3) Sozlash

`.env.example` ni `.env` ga ko‘chiring va to‘ldiring:

- `TG_API_ID`, `TG_API_HASH`
- (ixtiyoriy) `TG_PHONE`, `TG_PASSWORD` (2FA bo‘lsa)
- `SOURCE_CHATS` (vergul bilan) — masalan: `@sourcechannel1,-1001234567890`
- `DEST_CHAT` — masalan: `@destchannel` yoki `-100...`
- `REPLACE_LINK_WITH`, `REPLACE_PHONE_WITH`, `REPLACE_USERNAME_WITH`
 
Ixtiyoriy:

- `DRY_RUN=1` — yubormaydi, faqat log qiladi.

## 4) Ishga tushirish

```powershell
.\.venv\Scripts\Activate.ps1
python .\main.py
```

Birinchi ishga tushirganda Telegram sizdan telefon raqam va SMS/Telegram kodini so‘raydi (session fayl shu papkada saqlanadi).

## Eslatma

Session fayl (`*.session`) va `.env` ni hech kimga yubormang.

Telegram qoidalari va kanal huquqlariga rioya qiling.

## Serverga qo‘yish (interactive bo‘lmasa)

Ba’zi serverlar `/app` ichida `*.session` (SQLite) faylini ochishga ruxsat bermaydi yoki login uchun kod/2FA kiritib bo‘lmaydi. Bunday holatda `StringSession` ishlating:

1) Lokal kompyuterda bir marta login qiling (`python .\main.py`).
2) Session string oling: `python .\export_session_string.py`
3) Serverdagi `.env` ga qo‘ying: `TG_SESSION_STRING=...` va `NON_INTERACTIVE=1`.

## Zip qilib yuborish

Yuborish uchun `.venv/`, `__pycache__/`, `*.session*`, `.env` kabi fayllarni zip’ga qo‘shmang (zip ham kichraymaydi, aksincha katta bo‘lib ketadi).

Tayyor zip yaratish:

```powershell
.\pack.ps1
```

Natija: `project.zip` (ichida `main.py` va `requirements.txt` root’da bo‘ladi).

Agar siz zip’ni boshqa server/bot orqali ishga tushirmoqchi bo‘lsangiz va u yerda `.env`/OTP/2FA kiritish imkoni bo‘lmasa, unda avval bu loyiha lokal kompyuteringizda 1 marta login qilib `*.session*` fayl yaratib oling. Keyin **secrets bilan** zip qiling:

```powershell
.\pack.ps1 -IncludeSecrets -Destination deploy.zip
```

Diqqat: `deploy.zip` ichida `.env` va `*.session*` bo‘ladi — bular maxfiy.
