import asyncio
import html
import json
import os
import re
import sys
import threading
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telethon import TelegramClient, events, utils
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.custom.qrlogin import QRLogin
from telethon.tl.types import MessageEntityMentionName, MessageEntityTextUrl

CONFIG_PREFIX = "CONFIG_V1:"

URL_RE = re.compile(
    r"(?i)\b("
    r"https?://[^\s<>]+"
    r"|www\.[^\s<>]+"
    r"|t\.me/[^\s<>]+"
    r"|telegram\.me/[^\s<>]+"
    r")\b"
)
USERNAME_RE = re.compile(r"(?i)(?<!\w)@[a-z0-9_]{5,32}\b")
PHONE_RE = re.compile(r"(?x)(?<!\w)(\+?\d[\d\-\s\(\)]{7,}\d)\b")
CHAT_REF_RE = re.compile(r"^(?:@[A-Za-z0-9_]{3,32}|-?\d{6,}|-100\d{6,})$")


@dataclass(frozen=True)
class RuntimeSettings:
    bot_token: str
    db_chat: str
    owner_id: Optional[int]
    non_interactive: bool


@dataclass
class AppConfig:
    owner_id: Optional[int] = None
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    session_string: Optional[str] = None  # userbot StringSession
    source_chats: List[str] = field(default_factory=list)
    dest_chat: Optional[str] = None
    replace_link_with: str = "[removed]"
    replace_phone_with: str = "[removed]"
    replace_username_with: str = "[removed]"
    enabled: bool = False

    def is_ready(self) -> bool:
        if not self.api_id or not self.api_hash or not self.session_string:
            return False
        if not self.dest_chat or not _is_chat_ref(self.dest_chat):
            return False
        if not self.source_chats:
            return False
        if not all(_is_chat_ref(s) for s in self.source_chats):
            return False
        return bool(
            self.api_id
            and self.api_hash
            and self.session_string
            and self.source_chats
            and self.dest_chat
        )


@dataclass
class SetupState:
    stage: str
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone: Optional[str] = None
    phone_code_hash: Optional[str] = None
    temp_client: Optional[TelegramClient] = None
    qr: Optional[QRLogin] = None


class ConfigStoreError(RuntimeError):
    pass


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str) -> List[str]:
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def _is_chat_ref(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    return bool(CHAT_REF_RE.match(v))


def load_runtime_settings() -> RuntimeSettings:
    load_dotenv()
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    db_chat = os.environ.get("DB_CHAT", "").strip()
    owner_id_raw = os.environ.get("OWNER_ID", "").strip()

    if not bot_token:
        raise SystemExit("Missing BOT_TOKEN (set it in Render Environment or local .env).")
    if not db_chat:
        raise SystemExit("Missing DB_CHAT (e.g. @my_private_db_channel). Bot must be admin there.")

    owner_id: Optional[int] = None
    if owner_id_raw:
        try:
            owner_id = int(owner_id_raw)
        except ValueError as exc:
            raise SystemExit("OWNER_ID must be an integer.") from exc

    non_interactive = _parse_bool(os.environ.get("NON_INTERACTIVE", "0")) or not sys.stdin.isatty()
    return RuntimeSettings(bot_token=bot_token, db_chat=db_chat, owner_id=owner_id, non_interactive=non_interactive)


def sanitize_text(text: str, *, config: AppConfig, entities: Optional[Iterable[object]] = None) -> str:
    sanitized = text

    if entities:
        replacements: List[Tuple[int, int, str]] = []
        for ent in entities:
            if isinstance(ent, MessageEntityTextUrl):
                start = getattr(ent, "offset", None)
                length = getattr(ent, "length", None)
                if isinstance(start, int) and isinstance(length, int) and length > 0:
                    replacements.append((start, start + length, config.replace_link_with))
            elif isinstance(ent, MessageEntityMentionName):
                start = getattr(ent, "offset", None)
                length = getattr(ent, "length", None)
                if isinstance(start, int) and isinstance(length, int) and length > 0:
                    replacements.append((start, start + length, config.replace_username_with))
        for start, end, rep in sorted(replacements, key=lambda t: t[0], reverse=True):
            sanitized = sanitized[:start] + rep + sanitized[end:]

    sanitized = URL_RE.sub(config.replace_link_with, sanitized)
    sanitized = PHONE_RE.sub(config.replace_phone_with, sanitized)
    sanitized = USERNAME_RE.sub(config.replace_username_with, sanitized)
    return sanitized


def _main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Holat"), KeyboardButton(text="🆔 Mening ID")],
            [KeyboardButton(text="➕ Akkaunt ulash (userbot)")],
            [KeyboardButton(text="📥 Manba (source)"), KeyboardButton(text="📤 Manzil (dest)")],
            [KeyboardButton(text="🧹 Almashtirish (replace)")],
            [KeyboardButton(text="▶️ Ishga tushirish"), KeyboardButton(text="⏹ To‘xtatish")],
            [KeyboardButton(text="ℹ️ Yordam")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def _setup_method_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔳 QR orqali (tavsiya)")],
            [KeyboardButton(text="📱 Telefon/kod orqali")],
            [KeyboardButton(text="❌ Bekor qilish")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def _setup_qr_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Tekshirish"), KeyboardButton(text="🔄 Yangi QR")],
            [KeyboardButton(text="❌ Bekor qilish")],
        ],
        resize_keyboard=True,
        selective=True,
    )


def _is_menu_text(text: str, expected: str) -> bool:
    return (text or "").strip().lower() == expected.strip().lower()


def _maybe_start_health_server() -> None:
    port_raw = os.environ.get("PORT", "").strip()
    if not port_raw:
        return
    try:
        port = int(port_raw)
    except ValueError:
        print(f"WARNING: Invalid PORT={port_raw!r}; skipping health server.", flush=True)
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, fmt: str, *args: object) -> None:
            return

    def serve() -> None:
        try:
            httpd = HTTPServer(("0.0.0.0", port), Handler)
        except OSError as e:
            print(f"WARNING: Health server failed to bind on 0.0.0.0:{port}: {e}", flush=True)
            return
        httpd.serve_forever()

    threading.Thread(target=serve, name="health-server", daemon=True).start()
    print(f"Health server listening on 0.0.0.0:{port}", flush=True)


def _config_from_dict(data: dict) -> AppConfig:
    cfg = AppConfig()
    for key, value in data.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    if isinstance(cfg.source_chats, str):
        cfg.source_chats = _split_csv(cfg.source_chats)
    if cfg.source_chats is None:
        cfg.source_chats = []
    return cfg


async def load_config(bot: Bot, db_chat: str) -> AppConfig:
    try:
        chat = await bot.get_chat(db_chat)
    except Exception:
        return AppConfig()

    pinned = getattr(chat, "pinned_message", None)
    text = getattr(pinned, "text", None) or getattr(pinned, "caption", None) or ""
    text = text.strip()
    if text.startswith(CONFIG_PREFIX):
        payload = text[len(CONFIG_PREFIX) :].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return AppConfig()
        if isinstance(data, dict):
            return _config_from_dict(data)
    return AppConfig()


async def save_config(bot: Bot, db_chat: str, config: AppConfig) -> None:
    payload = json.dumps(asdict(config), ensure_ascii=False, separators=(",", ":"))
    text = f"{CONFIG_PREFIX}\n{payload}"
    try:
        chat = await bot.get_chat(db_chat)
    except Exception as e:
        raise SystemExit(f"DB_CHAT is not accessible by the bot: {type(e).__name__}: {e}") from e

    pinned = getattr(chat, "pinned_message", None)
    pinned_id = getattr(pinned, "message_id", None)
    if isinstance(pinned_id, int) and pinned_id > 0:
        try:
            await bot.edit_message_text(text=text, chat_id=db_chat, message_id=pinned_id, parse_mode=None)
            return
        except Exception:
            pass

    sent = await bot.send_message(chat_id=db_chat, text=text, disable_notification=True, parse_mode=None)
    try:
        await bot.pin_chat_message(chat_id=db_chat, message_id=sent.message_id, disable_notification=True)
    except Exception:
        raise ConfigStoreError(
            "DB_CHAT’da pin qilishga ruxsat yo‘q. Botni DB_CHAT’da admin qiling va 'Pin messages' huquqini bering."
        )


def _format_status(config: AppConfig, relay_running: bool) -> str:
    owner = str(config.owner_id) if config.owner_id else "o‘rnatilmagan"
    ready = "ha" if config.is_ready() else "yo‘q"
    enabled = "ha" if config.enabled else "yo‘q"
    sources = ", ".join(config.source_chats) if config.source_chats else "o‘rnatilmagan"
    dest = config.dest_chat or "o‘rnatilmagan"
    return (
        "📊 Holat:\n"
        f"- 👤 owner_id: {owner}\n"
        f"- ✅ tayyormi: {ready}\n"
        f"- 🔁 yoqilganmi: {enabled}\n"
        f"- 🟢 ishlayaptimi: {'ha' if relay_running else 'yo‘q'}\n"
        f"- 📥 manba: {sources}\n"
        f"- 📤 manzil: {dest}"
    )


class RelayManager:
    def __init__(self) -> None:
        self._client: Optional[TelegramClient] = None
        self._task: Optional[asyncio.Task] = None
        self._dest_peer_id: Optional[int] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._dest_peer_id = None

    async def start(self, config: AppConfig) -> None:
        if not config.is_ready():
            raise SystemExit("Relay config is incomplete. Run /setup and /set_source /set_dest first.")

        await self.stop()

        try:
            session = StringSession(config.session_string)
        except Exception as e:
            raise SystemExit("Invalid session_string. Re-run /setup to regenerate session.") from e

        client = TelegramClient(session, int(config.api_id), str(config.api_hash), flood_sleep_threshold=60)

        @client.on(events.NewMessage(chats=list(config.source_chats)))
        async def handler(event: events.NewMessage.Event) -> None:
            message = event.message
            if message is None:
                return
            if self._dest_peer_id is not None and event.chat_id == self._dest_peer_id:
                return

            raw_text = message.raw_text or ""
            cleaned_text = sanitize_text(raw_text, config=config, entities=message.entities)

            if message.photo or message.document:
                await client.send_file(
                    config.dest_chat,
                    file=message.media,
                    caption=cleaned_text if cleaned_text else None,
                )
            else:
                await client.send_message(config.dest_chat, cleaned_text)

        await client.connect()
        me = await client.get_me()
        if getattr(me, "bot", False):
            await client.disconnect()
            raise SystemExit("Userbot session is actually a bot session. Re-run /setup with a user account.")

        dest_entity = await client.get_entity(config.dest_chat)
        self._dest_peer_id = utils.get_peer_id(dest_entity)

        self._client = client
        self._task = asyncio.create_task(client.run_until_disconnected())


def _only_owner(runtime_owner_id: Optional[int], config_owner_id: Optional[int], sender_id: int) -> bool:
    effective = runtime_owner_id or config_owner_id
    return effective is None or sender_id == effective


def _command_name(text: str) -> str:
    first = (text or "").strip().split(maxsplit=1)[0]
    if not first:
        return ""
    if "@" in first:
        first = first.split("@", 1)[0]
    return first


async def main() -> None:
    settings = load_runtime_settings()
    print("Initializing...", flush=True)
    _maybe_start_health_server()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    try:
        me = await bot.get_me()
        print(f"Bot connected as @{me.username} (id={me.id})", flush=True)
    except Exception as e:
        raise SystemExit(f"BOT_TOKEN ishlamayapti: {type(e).__name__}: {e}") from e

    # If a webhook was previously set, polling (getUpdates) won't work.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("Webhook cleared (polling mode).", flush=True)
    except Exception as e:
        print(f"WARNING: delete_webhook failed: {type(e).__name__}: {e}", flush=True)

    print(f"Loading config from DB_CHAT={settings.db_chat!r} ...", flush=True)
    config = await load_config(bot, settings.db_chat)
    print("Config loaded.", flush=True)
    relay = RelayManager()
    setup_states: Dict[int, SetupState] = {}
    input_states: Dict[int, str] = {}

    async def persist(message: Optional[Message] = None) -> bool:
        try:
            await save_config(bot, settings.db_chat, config)
            return True
        except ConfigStoreError as e:
            if message is not None:
                await message.answer(
                    "❌ DB_CHAT ga saqlab bo‘lmadi.\n"
                    f"{e}\n\n"
                    "Tekshiring:\n"
                    "- DB_CHAT private kanal/guruh\n"
                    "- bot DB_CHAT’da admin\n"
                    "- botda Pin messages huquqi bor",
                    reply_markup=_main_menu_kb(),
                )
            return False
        except Exception as e:
            if message is not None:
                await message.answer(
                    f"❌ DB_CHAT ga saqlash xatosi: {type(e).__name__}: {e}",
                    reply_markup=_main_menu_kb(),
                )
            return False

    def help_text() -> str:
        return (
            "ℹ️ Yordam\n\n"
            "Bu bot serverda ishlaydi. Sozlashni shu chatda tugmalar orqali qilasiz.\n"
            "Akkaunt (userbot) ulab bo‘lgach, sozlamalar `DB_CHAT` ga pinned xabar sifatida saqlanadi.\n\n"
            "Server Environment (faqat shular kerak):\n"
            "- BOT_TOKEN\n"
            "- DB_CHAT\n"
            "- OWNER_ID (tavsiya)\n\n"
            "Tugmalar:\n"
            "- ➕ Akkaunt ulash — userbot login (telefon/kod/2FA)\n"
            "- 📥 Manba — qaysi kanal/guruhdan o‘qish\n"
            "- 📤 Manzil — qayerga yuborish\n"
            "- 🧹 Almashtirish — link/telefon/@username ni almashtirish\n"
            "- ▶️ Ishga tushirish / ⏹ To‘xtatish\n\n"
            "Qo‘shimcha buyruqlar (ixtiyoriy):\n"
            "/claim, /setup, /set_source, /set_dest, /set_replace, /start_relay, /stop_relay, /status, /cancel"
        )

    async def _safe_delete(message: Message) -> None:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except Exception:
            return

    @dp.message()
    async def on_message(message: Message) -> None:
        nonlocal config
        if message.chat.type != "private":
            return
        sender_id = message.from_user.id if message.from_user else None
        if sender_id is None:
            return

        text = (message.text or "").strip()
        if not text:
            return

        cmd = _command_name(text)

        if cmd == "/start":
            await message.answer(
                "Assalomu alaykum!\n"
                "Menyudan foydalaning.\n\n"
                "Birinchi marta bo‘lsa: /claim.\n"
                "Agar 'Ruxsat yo‘q' chiqsa: '🆔 Mening ID' ni olib Render’da OWNER_ID ni to‘g‘ri qo‘ying.",
                reply_markup=_main_menu_kb(),
            )
            return

        if cmd == "/help" or _is_menu_text(text, "ℹ️ Yordam"):
            await message.answer(help_text(), reply_markup=_main_menu_kb())
            return

        if cmd == "/myid" or _is_menu_text(text, "🆔 Mening ID"):
            await message.answer(f"🆔 Sizning Telegram ID: <code>{sender_id}</code>", reply_markup=_main_menu_kb())
            return

        if cmd == "/claim":
            if settings.owner_id is not None and sender_id != settings.owner_id:
                await message.answer(
                    "❌ Ruxsat yo‘q.\n"
                    "Bot serverda OWNER_ID bilan lock qilingan.\n"
                    f"Sizning ID: <code>{sender_id}</code>",
                    reply_markup=_main_menu_kb(),
                )
                return
            if config.owner_id is not None and sender_id != config.owner_id:
                await message.answer("❌ Ruxsat yo‘q. Owner avvalroq o‘rnatilgan.", reply_markup=_main_menu_kb())
                return
            config.owner_id = sender_id
            await persist(message)
            await message.answer("✅ Owner o‘rnatildi.", reply_markup=_main_menu_kb())
            return

        if not _only_owner(settings.owner_id, config.owner_id, sender_id):
            await message.answer("❌ Ruxsat yo‘q. '🆔 Mening ID' ni oling va OWNER_ID ni to‘g‘ri qo‘ying.")
            return

        if cmd == "/status" or _is_menu_text(text, "📊 Holat"):
            await message.answer(_format_status(config, relay.running), reply_markup=_main_menu_kb())
            return

        if cmd == "/cancel":
            st = setup_states.pop(sender_id, None)
            if st and st.temp_client is not None:
                await st.temp_client.disconnect()
            input_states.pop(sender_id, None)
            await message.answer("✅ Bekor qilindi.", reply_markup=_main_menu_kb())
            return

        if _is_menu_text(text, "❌ Bekor qilish"):
            st = setup_states.pop(sender_id, None)
            if st and st.temp_client is not None:
                await st.temp_client.disconnect()
            input_states.pop(sender_id, None)
            await message.answer("✅ Bekor qilindi.", reply_markup=_main_menu_kb())
            return

        if cmd == "/set_source":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.answer("📥 Manba: /set_source @kanal1,@kanal2", reply_markup=_main_menu_kb())
                return
            sources = _split_csv(parts[1])
            if not sources or not all(_is_chat_ref(s) for s in sources):
                await message.answer(
                    "❌ Noto‘g‘ri format.\n"
                    "Misol: <code>@kanal1,@kanal2</code> yoki <code>-100...</code>",
                    reply_markup=_main_menu_kb(),
                )
                return
            config.source_chats = sources
            await persist(message)
            await message.answer("✅ Saqlandi.", reply_markup=_main_menu_kb())
            if config.enabled and config.is_ready():
                try:
                    await relay.start(config)
                except Exception as e:
                    await message.answer(f"❌ Relay xato: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
            return

        if _is_menu_text(text, "📥 Manba (source)"):
            input_states[sender_id] = "source"
            await message.answer(
                "📥 Manba kiriting.\n"
                "Misol: <code>@kanal1,@kanal2</code> yoki <code>-100...</code>\n"
                "Bekor qilish: /cancel",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if cmd == "/set_dest":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.answer("📤 Manzil: /set_dest @dest", reply_markup=_main_menu_kb())
                return
            dest = parts[1].strip()
            if not _is_chat_ref(dest):
                await message.answer(
                    "❌ Noto‘g‘ri format.\n"
                    "Misol: <code>@dest</code> yoki <code>-100...</code>",
                    reply_markup=_main_menu_kb(),
                )
                return
            config.dest_chat = dest
            await persist(message)
            await message.answer("✅ Saqlandi.", reply_markup=_main_menu_kb())
            if config.enabled and config.is_ready():
                try:
                    await relay.start(config)
                except Exception as e:
                    await message.answer(f"❌ Relay xato: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
            return

        if _is_menu_text(text, "📤 Manzil (dest)"):
            input_states[sender_id] = "dest"
            await message.answer(
                "📤 Manzil kiriting.\n"
                "Misol: <code>@dest</code> yoki <code>-100...</code>\n"
                "Bekor qilish: /cancel",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        if cmd == "/set_replace" or _is_menu_text(text, "🧹 Almashtirish (replace)"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                input_states[sender_id] = "replace"
                await message.answer(
                    "🧹 Almashtirish sozlang.\n"
                    "Misol:\n"
                    "<code>link=[removed] phone=[removed] user=@removed</code>\n"
                    "Bekor qilish: /cancel",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return
            input_text = parts[1]
            kv = {}
            for token in input_text.split():
                if "=" not in token:
                    continue
                k, v = token.split("=", 1)
                kv[k.strip().lower()] = v.strip()
            if "link" in kv:
                config.replace_link_with = kv["link"]
            if "phone" in kv:
                config.replace_phone_with = kv["phone"]
            if "user" in kv:
                config.replace_username_with = kv["user"]
            await persist(message)
            await message.answer("✅ Saqlandi.", reply_markup=_main_menu_kb())
            return

        if cmd == "/stop_relay" or _is_menu_text(text, "⏹ To‘xtatish"):
            config.enabled = False
            await persist(message)
            await relay.stop()
            await message.answer("⏹ To‘xtatildi.", reply_markup=_main_menu_kb())
            return

        if cmd == "/start_relay" or _is_menu_text(text, "▶️ Ishga tushirish"):
            if not config.is_ready():
                await message.answer("❌ Tayyor emas. Avval '➕ Akkaunt ulash', so‘ng Manba va Manzilni kiriting.")
                return
            config.enabled = True
            await persist(message)
            try:
                await relay.start(config)
            except Exception as e:
                config.enabled = False
                await persist(message)
                await message.answer(f"❌ Ishga tushmadi: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
                return
            await message.answer("▶️ Ishga tushdi.", reply_markup=_main_menu_kb())
            return

        if cmd == "/setup" or _is_menu_text(text, "➕ Akkaunt ulash (userbot)"):
            if config.api_id and config.api_hash:
                setup_states[sender_id] = SetupState(
                    stage="method",
                    api_id=int(config.api_id),
                    api_hash=str(config.api_hash),
                )
                await message.answer(
                    "Ulash usulini tanlang.\n"
                    "🔳 QR — tavsiya (kodni chatga yuborish xavfli va Telegram bloklashi mumkin).",
                    reply_markup=_setup_method_kb(),
                )
                return
            setup_states[sender_id] = SetupState(stage="api_id")
            await message.answer("🔑 TG_API_ID yuboring (raqam).", reply_markup=ReplyKeyboardRemove())
            return

        if sender_id in input_states:
            mode = input_states.pop(sender_id)
            if mode == "source":
                sources = _split_csv(text)
                if not sources or not all(_is_chat_ref(s) for s in sources):
                    await message.answer(
                        "❌ Noto‘g‘ri format.\n"
                        "Misol: <code>@kanal1,@kanal2</code> yoki <code>-100...</code>",
                        reply_markup=_main_menu_kb(),
                    )
                    return
                config.source_chats = sources
                await persist(message)
                await message.answer("✅ Manba saqlandi.", reply_markup=_main_menu_kb())
                if config.enabled and config.is_ready():
                    try:
                        await relay.start(config)
                    except Exception as e:
                        await message.answer(f"❌ Relay xato: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
                return
            if mode == "dest":
                dest = text.strip()
                if not _is_chat_ref(dest):
                    await message.answer(
                        "❌ Noto‘g‘ri format.\n"
                        "Misol: <code>@dest</code> yoki <code>-100...</code>",
                        reply_markup=_main_menu_kb(),
                    )
                    return
                config.dest_chat = dest
                await persist(message)
                await message.answer("✅ Manzil saqlandi.", reply_markup=_main_menu_kb())
                if config.enabled and config.is_ready():
                    try:
                        await relay.start(config)
                    except Exception as e:
                        await message.answer(f"❌ Relay xato: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
                return
            if mode == "replace":
                kv = {}
                for token in text.split():
                    if "=" not in token:
                        continue
                    k, v = token.split("=", 1)
                    kv[k.strip().lower()] = v.strip()
                if "link" in kv:
                    config.replace_link_with = kv["link"]
                if "phone" in kv:
                    config.replace_phone_with = kv["phone"]
                if "user" in kv:
                    config.replace_username_with = kv["user"]
                await persist(message)
                await message.answer("✅ Saqlandi.", reply_markup=_main_menu_kb())
                return

        # Setup wizard flow
        if sender_id in setup_states:
            st = setup_states[sender_id]
            if st.stage == "api_id":
                try:
                    st.api_id = int(text)
                except ValueError:
                    await message.answer("❌ TG_API_ID raqam bo‘lishi kerak. Qayta yuboring.")
                    return
                st.stage = "api_hash"
                await message.answer("🔑 TG_API_HASH yuboring (string).")
                return

            if st.stage == "api_hash":
                if len(text) < 10:
                    await message.answer("❌ TG_API_HASH juda qisqa ko‘rinadi. Qayta yuboring.")
                    return
                st.api_hash = text
                config.api_id = int(st.api_id)
                config.api_hash = str(st.api_hash)
                await persist(message)
                st.stage = "method"
                await message.answer(
                    "Ulash usulini tanlang.\n"
                    "🔳 QR — tavsiya (kodni chatga yuborish xavfli va Telegram bloklashi mumkin).",
                    reply_markup=_setup_method_kb(),
                )
                return

            if st.stage == "method":
                if _is_menu_text(text, "🔳 QR orqali (tavsiya)"):
                    temp = TelegramClient(StringSession(), int(st.api_id), str(st.api_hash), flood_sleep_threshold=60)
                    await temp.connect()
                    st.temp_client = temp
                    st.qr = await temp.qr_login()
                    st.stage = "qr_wait"
                    url = html.escape(st.qr.url)
                    await message.answer(
                        "🔳 QR orqali ulash.\n\n"
                        "1) Quyidagi linkni bosing (Telegram o‘zi tasdiqlash oynasini ochadi):\n"
                        f"<code>{url}</code>\n\n"
                        "2) Telegram’da tasdiqlang.\n"
                        "3) Keyin '✅ Tekshirish' ni bosing.",
                        reply_markup=_setup_qr_kb(),
                    )
                    return

                if _is_menu_text(text, "📱 Telefon/kod orqali"):
                    st.stage = "phone"
                    await message.answer(
                        "📱 Telefon raqam yuboring. Misol: <code>+998901234567</code>\n\n"
                        "Eslatma: Telegram kodni chatga yuborsangiz login’ni bloklashi mumkin. QR tavsiya.",
                        reply_markup=ReplyKeyboardRemove(),
                    )
                    return

                await message.answer("Ulash usulini tanlang:", reply_markup=_setup_method_kb())
                return

            if st.stage == "qr_wait":
                if st.temp_client is None or st.qr is None:
                    setup_states.pop(sender_id, None)
                    await message.answer("❌ Xatolik. '➕ Akkaunt ulash' ni qaytadan boshlang.", reply_markup=_main_menu_kb())
                    return

                if _is_menu_text(text, "🔄 Yangi QR"):
                    try:
                        st.qr = await st.qr.recreate()
                    except Exception as e:
                        await message.answer(f"❌ QR yangilanmadi: {type(e).__name__}: {e}", reply_markup=_setup_qr_kb())
                        return
                    url = html.escape(st.qr.url)
                    await message.answer(
                        "🔄 Yangi QR tayyor.\n"
                        "Link:\n"
                        f"<code>{url}</code>\n\n"
                        "Telegram’da tasdiqlang, keyin '✅ Tekshirish'.",
                        reply_markup=_setup_qr_kb(),
                    )
                    return

                if _is_menu_text(text, "✅ Tekshirish"):
                    try:
                        await asyncio.wait_for(st.qr.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        await message.answer("⌛ Hali ulanmagan. Telegram’da linkni tasdiqlang, so‘ng yana tekshiring.", reply_markup=_setup_qr_kb())
                        return
                    except SessionPasswordNeededError:
                        st.stage = "password"
                        await message.answer(
                            "🔐 2FA yoqilgan. Telegram 2FA parolingizni yuboring.\n"
                            "Diqqat: bu maxfiy ma’lumot. Ishonchingiz bo‘lmasa, bu usuldan foydalanmang.",
                            reply_markup=ReplyKeyboardRemove(),
                        )
                        return
                    except Exception as e:
                        await message.answer(f"❌ QR login xato: {type(e).__name__}: {e}", reply_markup=_setup_qr_kb())
                        return

                    if not await st.temp_client.is_user_authorized():
                        await message.answer("⌛ Hali avtorizatsiya bo‘lmadi. Birozdan so‘ng yana tekshiring.", reply_markup=_setup_qr_kb())
                        return

                    session_string = st.temp_client.session.save()
                    await st.temp_client.disconnect()
                    setup_states.pop(sender_id, None)

                    config.api_id = int(st.api_id)
                    config.api_hash = str(st.api_hash)
                    config.session_string = session_string
                    ok = await persist(message)
                    if not ok:
                        await message.answer(
                            "⚠️ Akkaunt ulandi, lekin DB_CHAT ga saqlanmadi. DB_CHAT huquqlarini to‘g‘rilab qayta /setup qiling.",
                            reply_markup=_main_menu_kb(),
                        )
                        return
                    await message.answer(
                        "✅ Akkaunt ulandi va DB_CHAT’ga saqlandi.\n\n"
                        "Keyingi qadamlar:\n"
                        "1) '📥 Manba (source)'\n"
                        "2) '📤 Manzil (dest)'\n"
                        "3) '▶️ Ishga tushirish'",
                        reply_markup=_main_menu_kb(),
                    )
                    return

            if st.stage == "phone":
                st.phone = text.strip()
                await _safe_delete(message)
                temp = TelegramClient(StringSession(), int(st.api_id), str(st.api_hash), flood_sleep_threshold=60)
                await temp.connect()
                try:
                    sent = await temp.send_code_request(st.phone)
                except Exception as e:
                    await temp.disconnect()
                    setup_states.pop(sender_id, None)
                    await message.answer(f"❌ Kod yuborilmadi: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
                    return
                st.temp_client = temp
                st.phone_code_hash = getattr(sent, "phone_code_hash", None)
                st.stage = "code"
                await message.answer(
                    "✅ Kod yuborildi.\n"
                    "Kodni yuboring (orasiga  nuqta qo'shib yuboring masalan 1.2.3.4.5.6).\n\n"
                    "Lekin xavfsizlik uchun 🔳 QR usuli tavsiya.",
                )
                return

            if st.stage == "code":
                if st.temp_client is None or not st.phone or not st.phone_code_hash:
                    setup_states.pop(sender_id, None)
                    await message.answer("❌ Xatolik. /setup ni qaytadan boshlang.", reply_markup=_main_menu_kb())
                    return
                code = re.sub(r"\D+", "", text)
                await _safe_delete(message)
                try:
                    await st.temp_client.sign_in(phone=st.phone, code=code, phone_code_hash=st.phone_code_hash)
                except PhoneCodeInvalidError:
                    await message.answer("❌ Kod noto‘g‘ri. Qayta yuboring.")
                    return
                except PhoneCodeExpiredError:
                    await st.temp_client.disconnect()
                    st.temp_client = None
                    st.phone = None
                    st.phone_code_hash = None
                    st.stage = "method"
                    await message.answer(
                        "❌ Kod eskirdi yoki Telegram login’ni blokladi.\n"
                        "Tavsiya: 🔳 QR orqali ulashni tanlang.",
                        reply_markup=_setup_method_kb(),
                    )
                    return
                except SessionPasswordNeededError:
                    st.stage = "password"
                    await message.answer("🔐 2FA yoqilgan. Telegram 2FA parolingizni yuboring.")
                    return
                except Exception as e:
                    await st.temp_client.disconnect()
                    setup_states.pop(sender_id, None)
                    await message.answer(f"❌ Login xato: {type(e).__name__}: {e}", reply_markup=_main_menu_kb())
                    return

                if not await st.temp_client.is_user_authorized():
                    await st.temp_client.disconnect()
                    setup_states.pop(sender_id, None)
                    await message.answer(
                        "❌ Kod qabul qilindi, lekin sessiya avtorizatsiya bo‘lmadi.\n"
                        "Qaytadan urinib ko‘ring: '➕ Akkaunt ulash (userbot)'.",
                        reply_markup=_main_menu_kb(),
                    )
                    return

                session_string = st.temp_client.session.save()
                await st.temp_client.disconnect()
                setup_states.pop(sender_id, None)

                config.api_id = int(st.api_id)
                config.api_hash = str(st.api_hash)
                config.session_string = session_string
                ok = await persist(message)
                if not ok:
                    await message.answer(
                        "⚠️ Akkaunt ulandi, lekin DB_CHAT ga saqlanmadi. DB_CHAT huquqlarini to‘g‘rilab qayta /setup qiling.",
                        reply_markup=_main_menu_kb(),
                    )
                    return
                await message.answer(
                    "✅ Akkaunt ulandi va DB_CHAT’ga saqlandi.\n\n"
                    "Keyingi qadamlar:\n"
                    "1) '📥 Manba (source)'\n"
                    "2) '📤 Manzil (dest)'\n"
                    "3) '▶️ Ishga tushirish'",
                    reply_markup=_main_menu_kb(),
                )
                return

            if st.stage == "password":
                if st.temp_client is None:
                    setup_states.pop(sender_id, None)
                    await message.answer("❌ Xatolik. /setup ni qaytadan boshlang.", reply_markup=_main_menu_kb())
                    return
                try:
                    await st.temp_client.sign_in(password=text)
                    await _safe_delete(message)
                except Exception as e:
                    await message.answer(f"❌ Parol xato: {type(e).__name__}: {e}")
                    return

                if not await st.temp_client.is_user_authorized():
                    await st.temp_client.disconnect()
                    setup_states.pop(sender_id, None)
                    await message.answer(
                        "❌ Parol qabul qilindi, lekin sessiya avtorizatsiya bo‘lmadi.\n"
                        "Qaytadan urinib ko‘ring: '➕ Akkaunt ulash (userbot)'.",
                        reply_markup=_main_menu_kb(),
                    )
                    return

                session_string = st.temp_client.session.save()
                await st.temp_client.disconnect()
                setup_states.pop(sender_id, None)

                config.api_id = int(st.api_id)
                config.api_hash = str(st.api_hash)
                config.session_string = session_string
                ok = await persist(message)
                if not ok:
                    await message.answer(
                        "⚠️ Akkaunt ulandi, lekin DB_CHAT ga saqlanmadi. DB_CHAT huquqlarini to‘g‘rilab qayta /setup qiling.",
                        reply_markup=_main_menu_kb(),
                    )
                    return
                await message.answer(
                    "✅ Akkaunt ulandi va DB_CHAT’ga saqlandi.\n\n"
                    "Keyingi qadamlar:\n"
                    "1) '📥 Manba (source)'\n"
                    "2) '📤 Manzil (dest)'\n"
                    "3) '▶️ Ishga tushirish'",
                    reply_markup=_main_menu_kb(),
                )
                return

        await message.answer("Tushunmadim. Menyudan tanlang yoki /help yuboring.", reply_markup=_main_menu_kb())

    if config.enabled and config.is_ready():
        try:
            await relay.start(config)
            print("Relay started from saved config.", flush=True)
        except Exception as e:
            print(f"WARNING: relay failed to start: {type(e).__name__}: {e}", flush=True)

    print("Bot is running. Open your bot and send /start", flush=True)
    print("Starting polling...", flush=True)
    try:
        await dp.start_polling(bot)
    except Exception as e:
        print(f"FATAL: polling crashed: {type(e).__name__}: {e}", flush=True)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}", flush=True)
        raise
