# backend_vpn

Django 4.2 + DRF backend for a VPN reseller service. Pairs with a Flutter
client in a separate repo (`vpn_app`) and a Telegram bot that lives inside
this one.

Python 3.11, PostgreSQL, Redis, MinIO (S3 via django-storages), Django
Channels on ASGI, python-telegram-bot 22.

## Before writing code

Read `SKILL.md` (django-flutter-craft) if it's available — it holds the
architecture conventions and review discipline this project is built on.

**Read every file you intend to change.** Do not write code against a guessed
signature. If a file matters and isn't in context, open it. If you genuinely
can't, say which assumption you made so it can be checked.

**Ask before deciding architecture.** Anything with more than one reasonable
answer — where a field lives, whether to soft-delete, how two flows should
interact — gets a question, not a choice. Fixing a wrong guess costs more than
asking.

## After writing code

```bash
python manage.py check              # admin/model errors are STARTUP errors
python manage.py makemigrations --check --dry-run
```

`manage.py check` first, always. A bad `list_filter` or a broken admin class
takes the whole app down, including unrelated management commands.

## Apps

```
apps/
├── account/      auth, users, profiles. Multi-identifier: email, phone or
│                 username, all optional, plus an internal `identifier`
│                 that is USERNAME_FIELD.
├── vpn/          plans, subscriptions, payments, 3x-ui provisioning
├── bot/          Telegram bot. Runs as its own long-polling container;
│                 the ASGI webhook view still exists but is not the
│                 production path. See Running.
├── referral/     invite codes, redemptions, admin-controlled gating
└── chat/         messaging, presence (Channels)
```

Every `__init__.py` stays **empty**, including in `views/` and
`serializers/`. Re-exporting there triggers `AppRegistryNotReady` at startup.
Import from the specific module:

```python
from apps.account.serializers.profile import UserInfoSerializer   # yes
from apps.account.serializers import UserInfoSerializer           # no
```

## Layering

```
views/        parse input, call a service, return an envelope. Thin.
services/     all business logic. Shared by REST views, the bot, and admin.
serializers/  shape in and out. No side effects.
models/       data + invariants as properties.
```

If a REST view and a bot handler both need to do something, the logic goes in
`services/` and both call it. Duplicating it means they drift.

## Response envelope

Everything returns `SuccessResponse` / `BadRequestResponse` /
`NotFoundResponse` / `ServerErrorResponse` from `config/utils/response.py`.
Domain errors are raised as `AppException` subclasses from services; the
custom exception handler converts them. Views don't build error dicts.

## Project conventions

- **`0` means unlimited** for volume, duration and concurrent users. Matches
  the 3x-ui panel. Use the `is_unlimited_*` properties, not `== 0`.
- **Migrations are generated locally and committed.** The server only runs
  `migrate`. Never `makemigrations` on the server.
- **Records are created only once earned.** A subscription exists only when a
  receipt has been submitted; an account exists only once its OTP or invite
  code is confirmed. No draft rows squatting on unique constraints.
- **Provision before approving.** `approve_payment_proof` calls the panel
  first and only then marks the proof approved — the reverse strands a paid
  customer with no retry path.
- **Soft delete anything that is evidence.** `hidden_at` on subscriptions;
  `is_active = False` on referral codes. Payment proofs must survive.
- **Counters that gate access need `select_for_update()` + `F()`.**
- **Admin-controlled policy is a single-row model** with `get_solo()` and
  `has_add_permission` returning False. See `AuthSettings`,
  `ReferralSettings`.

## External services

**3x-ui panel** — Bearer token auth. Read and write schemas differ, so prefer
delta endpoints (`bulkAdjust`) over whole-object writes (`updateClient`).
Never echo a fetched client object straight back.

**Telegram** — two delivery modes for the same handlers, and only one may
be active at a time (Telegram refuses `getUpdates` while a webhook is set):

- **Long polling** — `manage.py telegram_polling`, its own container. This is
  what production uses. It deletes the webhook on startup.
- **Webhook** — `apps/bot/views.py`, needs ASGI and a reachable public URL.

They fight. `app.sh` used to call `telegram_set_webhook` on every django
boot, which silently broke the running polling worker; that call is now
commented out. Re-enabling it means stopping the polling container in the
same change - a registered webhook makes `getUpdates` return 409, and the
bot goes quiet with no error anywhere near the button that stopped working.

Who may approve a payment receipt lives in `apps/bot/services/admin_access.py`:
`TELEGRAM_ADMIN_GROUP_CHAT_ID` (whoever is administrator/creator there) and
`TELEGRAM_ADMIN_USER_IDS` (a comma-separated allow-list; each gets their own
copy of the receipt in a private chat). A private chat has no admins —
`get_chat_member` answers `member` — so a private chat id in the *group*
setting authorises nobody. Group ids are negative; a positive id is a person.

**Kavenegar** — OTP goes through `verify/lookup`, not `sms/send`; the template
must be pre-approved in their panel. In `DEBUG` with no key the code is
logged instead of sent.

**MinIO** — two buckets. `media` is public (profile pictures, served direct).
`private` holds payment receipts and is streamed by a permission-checked
Django view, never linked to directly.

## Running

Service names are prefixed - there is no service called `django`:

```bash
docker compose up -d --build spacedigital_vpn_django
docker compose logs -f spacedigital_vpn_django
```

The bot is a **separate container** running long polling, so a change to
`apps/bot/` or to a Telegram setting needs this one restarted, not the web
container:

```bash
docker compose up -d --build spacedigital_vpn_telegram_bot
docker compose logs -f spacedigital_vpn_telegram_bot
```

The production database is named **`fitness_db`**, not `vpn_db` or `db` -
the server's volume was initialised under that name and postgres only
creates `POSTGRES_DB` on an empty volume, so renaming it in `.env` does not
create it, it just makes every container fail with `database "db" does not
exist` and leaves `app.sh` looping in its wait-for-postgres check.

ASGI is mandatory for the web container (`gunicorn config.asgi:application
-k uvicorn_worker.UvicornWorker`) — Channels needs it, and so does the
webhook view if you ever switch back to it. The polling worker is a plain
management command and doesn't care.

## When something breaks

Find the line that causes it and say which line. Don't tune a value until the
symptom goes away. If my description of the problem is wrong, say so.