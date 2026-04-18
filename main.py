import os
import re
import sys
from dataclasses import dataclass
from getpass import getpass
from typing import Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityMentionName, MessageEntityTextUrl


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    session_name: str
    session_string: str
    phone: str
    password: str
    source_chats: Sequence[str]
    dest_chat: str
    replace_link_with: str
    replace_phone_with: str
    replace_username_with: str
    dry_run: bool
    non_interactive: bool


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


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str) -> List[str]:
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def load_settings() -> Settings:
    load_dotenv()

    api_id_raw = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    session_name = os.environ.get("TG_SESSION", "userbot").strip() or "userbot"
    session_string = os.environ.get("TG_SESSION_STRING", "").strip()
    phone = os.environ.get("TG_PHONE", "").strip()
    password = os.environ.get("TG_PASSWORD", "").strip()
    source_chats_raw = os.environ.get("SOURCE_CHATS", "").strip()
    dest_chat = os.environ.get("DEST_CHAT", "").strip()

    if not api_id_raw or not api_hash:
        raise SystemExit("Missing TG_API_ID / TG_API_HASH. Copy .env.example to .env and fill it.")
    if not source_chats_raw:
        raise SystemExit("Missing SOURCE_CHATS (comma-separated).")
    if not dest_chat:
        raise SystemExit("Missing DEST_CHAT.")

    replace_link_with = os.environ.get("REPLACE_LINK_WITH", "[removed]").strip() or "[removed]"
    replace_phone_with = os.environ.get("REPLACE_PHONE_WITH", "[removed]").strip() or "[removed]"
    replace_username_with = os.environ.get("REPLACE_USERNAME_WITH", "[removed]").strip() or "[removed]"
    dry_run = _parse_bool(os.environ.get("DRY_RUN", "0"))
    non_interactive = _parse_bool(os.environ.get("NON_INTERACTIVE", "0")) or not sys.stdin.isatty()

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise SystemExit("TG_API_ID must be an integer.") from exc

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        session_string=session_string,
        phone=phone,
        password=password,
        source_chats=_split_csv(source_chats_raw),
        dest_chat=dest_chat,
        replace_link_with=replace_link_with,
        replace_phone_with=replace_phone_with,
        replace_username_with=replace_username_with,
        dry_run=dry_run,
        non_interactive=non_interactive,
    )


def sanitize_text(text: str, *, settings: Settings, entities: Optional[Iterable[object]] = None) -> str:
    sanitized = text

    # Replace hidden entities too (e.g. "text" -> URL, or clickable user mentions).
    if entities:
        replacements: List[Tuple[int, int, str]] = []
        for ent in entities:
            if isinstance(ent, MessageEntityTextUrl):
                start = getattr(ent, "offset", None)
                length = getattr(ent, "length", None)
                if isinstance(start, int) and isinstance(length, int) and length > 0:
                    replacements.append((start, start + length, settings.replace_link_with))
            elif isinstance(ent, MessageEntityMentionName):
                start = getattr(ent, "offset", None)
                length = getattr(ent, "length", None)
                if isinstance(start, int) and isinstance(length, int) and length > 0:
                    replacements.append((start, start + length, settings.replace_username_with))
        # Apply from the end to keep offsets stable
        for start, end, rep in sorted(replacements, key=lambda t: t[0], reverse=True):
            sanitized = sanitized[:start] + rep + sanitized[end:]

    sanitized = URL_RE.sub(settings.replace_link_with, sanitized)
    sanitized = PHONE_RE.sub(settings.replace_phone_with, sanitized)
    sanitized = USERNAME_RE.sub(settings.replace_username_with, sanitized)
    return sanitized


async def main() -> None:
    settings = load_settings()
    session = StringSession(settings.session_string) if settings.session_string else settings.session_name
    client = TelegramClient(
        session,
        settings.api_id,
        settings.api_hash,
        flood_sleep_threshold=60,
    )

    print("Userbot relay is running.", flush=True)
    print(f"Sources: {', '.join(settings.source_chats)}", flush=True)
    print(f"Dest: {settings.dest_chat}", flush=True)
    print("Connecting to Telegram...", flush=True)

    @client.on(events.NewMessage(chats=list(settings.source_chats)))
    async def handler(event: events.NewMessage.Event) -> None:
        message = event.message
        if message is None:
            return

        # Avoid infinite loops if DEST_CHAT is accidentally also in SOURCE_CHATS.
        if getattr(handler, "_dest_peer_id", None) is not None and event.chat_id == handler._dest_peer_id:
            return

        raw_text = message.raw_text or ""
        cleaned_text = sanitize_text(raw_text, settings=settings, entities=message.entities)

        if settings.dry_run:
            print(f"[DRY_RUN] from={event.chat_id} -> to={settings.dest_chat} text_len={len(cleaned_text)}", flush=True)
            return

        if message.photo or message.document:
            await client.send_file(
                settings.dest_chat,
                file=message.media,
                caption=cleaned_text if cleaned_text else None,
            )
        else:
            await client.send_message(settings.dest_chat, cleaned_text)

    try:
        await client.connect()
        print("Connected. Signing in as user...", flush=True)

        if settings.non_interactive and not await client.is_user_authorized():
            raise SystemExit(
                "Non-interactive mode detected and no authorized session is available. "
                "Fix: include an already-authorized session (TG_SESSION_STRING) or run once locally to create "
                "a .session file, then deploy with that session."
            )

        def password_cb() -> str:
            if settings.password:
                return settings.password
            if settings.non_interactive:
                raise SystemExit("2FA is enabled but TG_PASSWORD is not set (and non-interactive mode is on).")
            return getpass("Enter your Telegram 2FA password: ")

        await client.start(phone=settings.phone or None, password=password_cb)
        me = await client.get_me()
        if getattr(me, "bot", False):
            raise SystemExit(
                "This session is authorized as a bot. For userbot mode, set TG_SESSION to a NEW name "
                "(e.g. TG_SESSION=userbot_session) or delete the existing *.session file, then run again."
            )
        print("User authenticated successfully!", flush=True)

        dest_entity = await client.get_entity(settings.dest_chat)
        handler._dest_peer_id = utils.get_peer_id(dest_entity)

        print("Press Ctrl+C to stop.", flush=True)
        await client.run_until_disconnected()
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", flush=True)
        raise
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        import asyncio

        asyncio.run(main())
    except KeyboardInterrupt:
        pass
