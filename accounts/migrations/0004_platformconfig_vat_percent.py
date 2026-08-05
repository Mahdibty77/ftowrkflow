from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_platformconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfig",
            name="vat_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("10.00"),
                help_text="VAT percentage applied on Proforma Subtotal (e.g. 10).",
                max_digits=5,
            ),
        ),
    ]
