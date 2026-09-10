# This file must describe exactly what marketing.0001_initial actually
# created on every database where it has already been applied — including
# production, where it was applied on 2026-08-29 and is NOT safe to
# retroactively rewrite (Django tracks migrations as applied by name, not by
# content, so a database that already ran this migration will never re-run
# it no matter what this file says; only the STATE this file declares is
# used to compute what 0002 needs to do next). See 0002 for the real
# transition to the current models.py (ClientLabel) — this file intentionally
# does NOT depend on cases.0015_case_marketing_label, because on the
# database where it was really applied, that migration did not exist yet.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Entity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('field', models.CharField(db_index=True, max_length=32)),
                ('name', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['field', 'name'],
            },
        ),
        migrations.CreateModel(
            name='EntityLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('entity_a', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links_as_a', to='marketing.entity')),
                ('entity_b', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links_as_b', to='marketing.entity')),
            ],
        ),
        migrations.AddConstraint(
            model_name='entity',
            constraint=models.UniqueConstraint(fields=('field', 'name'), name='marketing_entity_unique_field_name'),
        ),
        migrations.AddConstraint(
            model_name='entitylink',
            constraint=models.UniqueConstraint(fields=('entity_a', 'entity_b'), name='marketing_link_unique_pair'),
        ),
        migrations.AddConstraint(
            model_name='entitylink',
            constraint=models.CheckConstraint(condition=~models.Q(entity_a=models.F('entity_b')), name='marketing_link_no_self'),
        ),
    ]
