"""Freeze the signer's seat alongside their name on an exported document.

``SignatureSnapshot`` already froze *who* signed (name + a copy of the
signature image) so a later rename or management change could not rewrite an
already-issued document. It did not freeze *what position they held*, which
left the same hole one level down: a promotion or a transfer silently restated
the rank next to the signature on every document that person had ever signed.

Purely additive: one blank-default column on the existing snapshot table.
Nothing reads it yet on old rows, and blank renders exactly as those documents
rendered before this field existed — the name alone, with no title — so no
historical document changes appearance as a result of applying this.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0005_backfill_event_actor_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="signaturesnapshot",
            name="signer_title",
            field=models.CharField(blank=True, max_length=160),
        ),
    ]
