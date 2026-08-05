"""One person, several seats.

The first cut of this module gave a person at most one account. That was wrong
about the organisation: the same human is the commercial manager and covers
purchasing, and each of those is a seat with its own unit, role and queue. So
``Person.user`` (a one-to-one) becomes ``PersonAccount`` — a row per holding,
with the one-to-one moved onto the account side, where it says the thing that
is actually true: a seat belongs to exactly one person.

``Person.suggested_username`` becomes ``Person.username`` in the same step. It
was never a suggestion — it is this human's sign-in name, and it is what the
accounts they hold are named from.

WHY THE ORDER IS WHAT IT IS
The new table and the new column are created first, the data is copied while
both shapes still exist, and only then are the old columns dropped. Every step
is reversible and the reverse copies the data back, so this can be rolled back
on a live server without losing which account belonged to whom.

In this deployment there is nothing to copy — 0001 has never been applied
anywhere, so the tables are made empty and stay empty. The copy is written
properly regardless: a migration that is correct only because a table happens
to be empty is a migration that fails the first time it is run somewhere else.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F

# How many rows are written per statement. Big enough that a few thousand
# people are a handful of round trips, small enough that nothing here has to
# hold a whole table in memory at once.
BATCH = 500


def forwards(apps, schema_editor):
    """Person.user -> a PersonAccount row; suggested_username -> username."""
    Person = apps.get_model("people", "Person")
    PersonAccount = apps.get_model("people", "PersonAccount")

    # assigned_at is auto_now_add, which bulk_create fills in. assigned_by
    # stays NULL, which is the honest answer: nobody performed this assignment,
    # a migration carried it across from the old shape.
    pairs = (Person.objects.filter(user_id__isnull=False)
             .values_list("id", "user_id").order_by("id"))
    PersonAccount.objects.bulk_create(
        [PersonAccount(person_id=person_id, user_id=user_id)
         for person_id, user_id in pairs.iterator()],
        batch_size=BATCH,
    )

    # A plain column copy: one UPDATE, no rows pulled into Python.
    Person.objects.update(username=F("suggested_username"))


def backwards(apps, schema_editor):
    """PersonAccount -> Person.user; username -> suggested_username.

    A person holding several seats cannot be expressed by the old shape at all,
    so the earliest-assigned seat is the one that survives. That is a real loss
    of information, and it is why this direction exists to back a bad deploy
    out rather than to be used routinely.
    """
    Person = apps.get_model("people", "Person")
    PersonAccount = apps.get_model("people", "PersonAccount")

    seen = set()
    batch = []
    for link in PersonAccount.objects.order_by("assigned_at", "pk").iterator():
        if link.person_id in seen:
            continue
        seen.add(link.person_id)
        # Only the two columns named below are written, so an instance built
        # from just those two is all bulk_update needs — and is the difference
        # between this and loading every person into memory.
        batch.append(Person(id=link.person_id, user_id=link.user_id))
        if len(batch) >= BATCH:
            Person.objects.bulk_update(batch, ["user"])
            batch = []
    if batch:
        Person.objects.bulk_update(batch, ["user"])

    Person.objects.update(suggested_username=F("username"))


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                ("assigned_by", models.ForeignKey(
                    blank=True, editable=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="seats_assigned", to=settings.AUTH_USER_MODEL)),
                ("person", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="accounts", to="people.person")),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="person_link", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Seat",
                "verbose_name_plural": "Seats",
                "ordering": ["user__username"],
            },
        ),
        migrations.AddField(
            model_name="person",
            name="username",
            field=models.CharField(blank=True, editable=False, max_length=150),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name="person",
            name="user",
        ),
        migrations.RemoveField(
            model_name="person",
            name="suggested_username",
        ),
    ]
