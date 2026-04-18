import os

from dotenv import load_dotenv
from telethon.sessions import SQLiteSession, StringSession


def main() -> None:
    load_dotenv()

    session_name = os.environ.get("TG_SESSION", "").strip() or "userbot"
    session_path = session_name
    if session_path.lower().endswith(".session"):
        session_path = session_path[: -len(".session")]

    sqlite_session = SQLiteSession(session_path)
    if not getattr(sqlite_session, "auth_key", None):
        raise SystemExit(
            f"Session '{session_name}' has no auth_key. Run main.py once locally and sign in first."
        )

    string_session = StringSession()
    string_session.set_dc(sqlite_session.dc_id, sqlite_session.server_address, sqlite_session.port)
    string_session.auth_key = sqlite_session.auth_key

    print(string_session.save())


if __name__ == "__main__":
    main()

