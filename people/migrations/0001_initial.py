"""Create the people directory.

Purely additive: two new tables, no change to any existing one, and no data
migration. In particular there is deliberately NO backfill — no person is
created for the accounts that already exist. Those accounts keep working
exactly as they do today, and each one gets a person when an administrator
enters that person and links them by hand.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import people.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonCounter",
            fields=[
                ("key", models.CharField(max_length=40, primary_key=True, serialize=False)),
                ("value", models.BigIntegerField(default=0)),
            ],
            options={
                "verbose_name": "Person counter",
                "verbose_name_plural": "Person counters",
            },
        ),
        migrations.CreateModel(
            name="Person",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("detail_code", models.CharField(
                    editable=False,
                    help_text="Assigned automatically on first save and never changes.",
                    max_length=20, unique=True, verbose_name="Detail code")),
                ("first_name", models.CharField(blank=True, max_length=80, verbose_name="نام")),
                ("last_name", models.CharField(max_length=120, verbose_name="نام خانوادگی")),
                ("first_name_en", models.CharField(blank=True, max_length=80,
                                                   verbose_name="Name (Latin)")),
                ("last_name_en", models.CharField(blank=True, max_length=120,
                                                  verbose_name="Last name (Latin)")),
                ("father_name", models.CharField(blank=True, max_length=80)),
                ("id_number", models.CharField(blank=True, max_length=20,
                                               verbose_name="شماره شناسنامه")),
                ("national_id", models.CharField(blank=True, max_length=10, null=True,
                                                 unique=True, verbose_name="کد ملی")),
                ("birth_date", models.DateField(blank=True, null=True)),
                ("birth_place", models.CharField(blank=True, max_length=80)),
                ("gender", models.CharField(blank=True, max_length=10)),
                ("marital", models.CharField(blank=True, max_length=20)),
                ("military", models.CharField(blank=True, max_length=30)),
                ("city", models.CharField(blank=True, max_length=80)),
                ("mobile", models.CharField(blank=True, max_length=20)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address", models.CharField(blank=True, max_length=255)),
                ("photo", models.ImageField(
                    blank=True, null=True, upload_to=people.models.person_photo_path)),
                ("resume", models.FileField(
                    blank=True, null=True, upload_to=people.models.person_document_path)),
                ("card_number", models.CharField(blank=True, max_length=20)),
                ("iban", models.CharField(blank=True, max_length=26, verbose_name="شبا")),
                ("bank_name", models.CharField(blank=True, max_length=60)),
                ("account_holder", models.CharField(blank=True, max_length=120)),
                ("education", models.JSONField(blank=True, default=list)),
                ("employment", models.JSONField(blank=True, default=list)),
                ("courses", models.JSONField(blank=True, default=list)),
                ("motivation", models.JSONField(blank=True, default=dict)),
                ("references", models.JSONField(blank=True, default=dict)),
                ("request", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(
                    choices=[("ACTIVE", "Active"), ("DEPARTED", "Departed")],
                    db_index=True, default="ACTIVE", max_length=20)),
                ("job_title", models.CharField(blank=True, max_length=120)),
                ("hired_on", models.DateField(blank=True, null=True)),
                ("departed_on", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("suggested_username", models.CharField(blank=True, editable=False,
                                                        max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(
                    blank=True, editable=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_people", to=settings.AUTH_USER_MODEL)),
                ("user", models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="person", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["last_name", "first_name"],
                "verbose_name_plural": "People",
            },
        ),
        migrations.AddIndex(
            model_name="person",
            index=models.Index(fields=["last_name", "first_name"],
                               name="people_person_name_idx"),
        ),
    ]
