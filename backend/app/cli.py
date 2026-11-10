"""StockMate administrative CLI.

Usage (from the backend directory):

    python -m app.cli create-admin [--username NAME]

Creates administrator accounts interactively:
- the password is typed hidden (getpass), never printed or logged
- no default or hardcoded credentials exist anywhere
- password strength is enforced (app/auth/passwords.py)
"""

import argparse
import getpass
import re
import sys

from sqlalchemy import select

from app.auth.passwords import hash_password, validate_password_strength
from app.config import get_settings
from app.database import create_db_engine, create_session_factory
from app.db_admin import run_migrations
from app.models import User

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,50}$")


def create_admin(username: str | None) -> int:
    settings = get_settings()
    run_migrations(settings.database_url)

    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    if not username:
        username = input("Admin username: ").strip()
    if not USERNAME_PATTERN.match(username):
        print("Invalid username: use 3-50 characters of letters, digits, '.', '_' or '-'.")
        return 1

    password = getpass.getpass("Admin password: ")
    password_repeat = getpass.getpass("Repeat password: ")
    if password != password_repeat:
        print("Passwords do not match.")
        return 1
    errors = validate_password_strength(password)
    if errors:
        print("Password too weak - it must contain: " + ", ".join(errors) + ".")
        return 1

    with session_factory() as session:
        existing = session.scalar(select(User).where(User.username == username))
        if existing is not None:
            print(f"User '{username}' already exists. No changes made.")
            return 1
        session.add(User(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        ))
        session.commit()

    print(f"Administrator '{username}' created. The password is not shown again.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="stockmate", description="StockMate admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="Create an administrator account")
    create.add_argument("--username", help="Username for the new administrator")
    args = parser.parse_args()

    if args.command == "create-admin":
        sys.exit(create_admin(args.username))


if __name__ == "__main__":
    main()
