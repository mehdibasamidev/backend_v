# backend_vpn

Django 4.2 + DRF backend for a VPN reseller service. Pairs with a Flutter
client in a separate repo (`vpn_app`) and a Telegram bot that lives inside
this one.

Python 3.11, PostgreSQL, Redis, MinIO (S3 via django-storages), Django
Channels on ASGI, python-telegram-bot 21.

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
├── bot/          Telegram bot (webhook, ASGI)
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

**Kavenegar** — OTP goes through `verify/lookup`, not `sms/send`; the template
must be pre-approved in their panel. In `DEBUG` with no key the code is
logged instead of sent.

**MinIO** — two buckets. `media` is public (profile pictures, served direct).
`private` holds payment receipts and is streamed by a permission-checked
Django view, never linked to directly.

## Running

```bash
docker compose up -d --build django
docker compose logs -f django
```

ASGI is mandatory (`gunicorn config.asgi:application -k
uvicorn_worker.UvicornWorker`). The Telegram webhook and Channels both need
it; under WSGI the bot silently receives nothing.

## When something breaks

Find the line that causes it and say which line. Don't tune a value until the
symptom goes away. If my description of the problem is wrong, say so.