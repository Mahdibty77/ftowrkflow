from django.db import migrations, models
import django.utils.timezone


def copy_created_to_updated(apps, schema_editor):
    CaseForm = apps.get_model("cases", "CaseForm")
    for form in CaseForm.objects.all().iterator():
        CaseForm.objects.filter(pk=form.pk).update(updated_at=form.created_at)


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="caseform",
            name="updated_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.RunPython(copy_created_to_updated, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="caseform",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
