"""
Seeds the default inbound group from XUI_DEFAULT_INBOUND_IDS.

That variable used to be read on every activation. Inbounds now live in
the database (admin panel "Inbounds" tab, or the Django admin) and
settings.py no longer defines it, so it is read here exactly once, straight
from the environment. That way the first deploy keeps provisioning onto the
same inbounds as before without anyone re-entering them. Once this has run,
the variable can be deleted from .env.
"""

from decouple import config
from django.db import migrations


def _parse_panel_ids(raw):
    panel_ids = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        # The old runtime parser was a bare int() and crashed on the same
        # input; failing here names the variable instead of a traceback.
        if not token.isdecimal():
            raise ValueError(
                f"XUI_DEFAULT_INBOUND_IDS contains {token!r}, which is not an "
                "inbound id. Set it to comma-separated panel inbound ids "
                "(e.g. 1,2,3) or leave it empty, then run migrate again."
            )
        panel_ids.append(int(token))
    return list(dict.fromkeys(panel_ids))


def seed_default_group(apps, schema_editor):
    XuiInbound = apps.get_model("vpn", "XuiInbound")
    InboundGroup = apps.get_model("vpn", "InboundGroup")

    panel_ids = _parse_panel_ids(config("XUI_DEFAULT_INBOUND_IDS", default=""))

    # A default group must always exist - custom plans have nowhere else to
    # go. It may end up empty, in which case approval says so explicitly.
    group = InboundGroup.objects.filter(is_default=True).first()
    if group is None:
        group, _ = InboundGroup.objects.get_or_create(name="Default")
        if not group.is_default:
            group.is_default = True
            group.save(update_fields=["is_default"])

    for panel_id in panel_ids:
        # Remark/protocol/port stay blank until the first sync from the panel.
        inbound, _ = XuiInbound.objects.get_or_create(panel_id=panel_id)
        group.inbounds.add(inbound)


class Migration(migrations.Migration):

    dependencies = [
        ("vpn", "0004_inbound_groups_and_proof_outbox"),
    ]

    operations = [
        migrations.RunPython(seed_default_group, migrations.RunPython.noop),
    ]
