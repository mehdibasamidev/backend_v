import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from telegram import Bot
from telegram.error import TelegramError
from telegram.request import HTTPXRequest


MAX_UPLOAD_BYTES = 50_000_000


def _dump_encrypted(path, recipient):
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise CommandError("This command backs up the default PostgreSQL database.")

    dump_env = {
        **os.environ,
        "PGHOST": database.get("HOST") or "",
        "PGPORT": str(database.get("PORT") or "5432"),
        "PGUSER": database["USER"],
        "PGDATABASE": database["NAME"],
        "PGPASSWORD": database["PASSWORD"],
        "PGCONNECT_TIMEOUT": "10",
    }
    # Stream plaintext into age so no unencrypted dump is written to disk.
    with path.open("wb") as output, subprocess.Popen(
        ["pg_dump", "--format=custom", "--no-password"],
        env=dump_env,
        stdout=subprocess.PIPE,
    ) as dump:
        try:
            subprocess.run(
                ["age", "--encrypt", "--recipient", recipient],
                stdin=dump.stdout,
                stdout=output,
                check=True,
            )
        finally:
            dump.stdout.close()
        if dump.wait() != 0:
            raise CommandError("pg_dump failed. Nothing was uploaded.")


async def _send(path, chat_id):
    # HTTP request URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    request = HTTPXRequest(proxy=settings.TELEGRAM_PROXY_URL or None)
    async with Bot(token=settings.TELEGRAM_BOT_TOKEN, request=request) as bot:
        chat = await bot.get_chat(chat_id)
        if chat.type != "private" or chat.id != chat_id:
            raise CommandError("The backup destination must be the configured private Telegram chat.")
        with path.open("rb") as encrypted:
            await bot.send_document(chat_id, encrypted, filename=path.name, write_timeout=180)


class Command(BaseCommand):
    help = "Send an age-encrypted PostgreSQL dump to the configured private Telegram chat."

    def add_arguments(self, parser):
        parser.add_argument("--info", action="store_true", help="Show the live database connection without uploading.")

    def handle(self, *args, **options):
        if options["info"]:
            database = connections["default"]
            if database.vendor != "postgresql":
                raise CommandError("The default connection is not PostgreSQL.")
            try:
                with database.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_database(), inet_server_addr(), inet_server_port(), pg_is_in_recovery()"
                    )
                    name, address, port, replica = cursor.fetchone()
            except DatabaseError as exc:
                raise CommandError(f"Could not query the configured database ({type(exc).__name__}).") from None
            self.stdout.write(f"Configured host: {database.settings_dict['HOST'] or '(local socket)'}")
            self.stdout.write(f"Connected database: {name}, server: {address}:{port}, replica: {replica}")
            return

        try:
            chat_id = int(settings.TELEGRAM_BACKUP_CHAT_ID)
            if chat_id <= 0:
                raise ValueError
        except ValueError:
            raise CommandError("Set TELEGRAM_BACKUP_CHAT_ID to your positive private chat ID.") from None

        recipient = settings.DATABASE_BACKUP_AGE_RECIPIENT.strip()
        if not recipient.startswith("age1"):
            raise CommandError("Set DATABASE_BACKUP_AGE_RECIPIENT to your age public key.")
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Set TELEGRAM_BOT_TOKEN before sending a backup.")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with TemporaryDirectory(prefix="vpn-db-backup-") as directory:
            path = Path(directory) / f"vpn-db-{timestamp}.dump.age"
            try:
                _dump_encrypted(path, recipient)
            except (OSError, subprocess.CalledProcessError):
                raise CommandError("Could not create the encrypted backup. Check pg_dump and age.") from None
            if not 0 < path.stat().st_size <= MAX_UPLOAD_BYTES:
                raise CommandError("The encrypted backup is empty or exceeds Telegram's 50 MB upload limit.")
            try:
                asyncio.run(_send(path, chat_id))
            except (TelegramError, ValueError) as exc:
                # Never include exception text from Telegram's HTTP layer.
                raise CommandError(f"Telegram upload failed ({type(exc).__name__}).") from None
        self.stdout.write(self.style.SUCCESS(f"Sent {path.name} to the configured private chat."))
