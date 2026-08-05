"""Audit log for items created through the Technical Assistant (EA).

Purely additive: one new table, nothing existing touched. Does not affect
CodeTable/CodeTableRow/FeatureValue or any coding/pricing logic in any way —
this only records what EA did, after the fact, for review in Tool Data.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("itemcoder", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EaItemCreationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_username", models.CharField(blank=True, max_length=150)),
                ("group", models.CharField(db_index=True, max_length=40)),
                ("item_type", models.CharField(blank=True, max_length=80)),
                ("selected_values", models.JSONField(blank=True, default=dict)),
                ("new_feature", models.CharField(blank=True, max_length=80)),
                ("new_value", models.CharField(blank=True, max_length=200)),
                ("technical_code", models.CharField(blank=True, max_length=64)),
                ("item_code", models.CharField(blank=True, max_length=64)),
                ("case_id", models.PositiveIntegerField(blank=True, null=True)),
                ("row_client_no", models.CharField(blank=True, max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("user", models.ForeignKey(null=True, on_delete=models.SET_NULL,
                                           related_name="ea_item_creations",
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["group", "-created_at"], name="itemcoder_ea_grp_dt_idx")],
            },
        ),
    ]
