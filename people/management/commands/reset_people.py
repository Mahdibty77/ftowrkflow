"""Empty the people directory and put every seat back the way it was.

For a server that is still being set up: an administrator enters a dozen people
to see how the screens behave, then wants a clean slate before the real data
goes in. Doing it by hand means deleting rows in the right order and renaming
the affected accounts one at a time, and getting that wrong leaves accounts
walking around wearing the names of people who no longer exist.

What it does, and only this:

*Every seat is released first*, through the same code path the seats page uses
— so each account's name and username go back to a neutral placeholder before
anything is deleted. This is the whole reason the command exists; deleting the
Person rows alone would leave the renames behind with nothing left to explain
them.

*Then every Person is deleted*, and the detail-code counter is wound back to
the start so the next person entered is 100000001 again.

What it never touches: passwords, is_active, profiles (unit, role, title,
signature, stamp), permissions, cases, documents, timelines. Nothing outside
this app is deleted, and no account is created or removed.

Requires --yes, because it cannot be undone::

    python manage.py reset_people --yes
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from people.constants import DETAIL_CODE_COUNTER_KEY, DETAIL_CODE_START
from people.models import Person, PersonAccount, PersonCounter
from people.seats import release_seat


class Command(BaseCommand):
    help = "Delete every person, release every seat, and reset the detail-code counter."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true",
            help="Confirm. Without it the command reports what it would do and stops.",
        )
        parser.add_argument(
            "--keep-counter", action="store_true",
            help="Leave the detail-code counter where it is, so codes carry on "
                 "rising instead of restarting at 100000001.",
        )

    def handle(self, *args, **options):
        people_count = Person.objects.count()
        seat_count = PersonAccount.objects.count()

        if not options["yes"]:
            self.stdout.write(
                f"Would release {seat_count} seat(s) and delete {people_count} "
                f"person record(s). Re-run with --yes to do it.")
            return

        # Seats are released one at a time and outside the delete, because each
        # one renames an account — a bulk delete would drop the links and leave
        # every one of those accounts still carrying somebody's name.
        released = 0
        for link in PersonAccount.objects.select_related("user").order_by("pk"):
            try:
                freed = release_seat(link)
            except Exception as exc:      # noqa: BLE001 - reported, not swallowed
                raise CommandError(
                    f"Could not release the seat on user {link.user_id}: {exc}")
            released += 1
            self.stdout.write(f"  released {link.user_id} -> {freed}")

        with transaction.atomic():
            deleted, _detail = Person.objects.all().delete()
            if not options["keep_counter"]:
                PersonCounter.objects.update_or_create(
                    key=DETAIL_CODE_COUNTER_KEY,
                    defaults={"value": DETAIL_CODE_START - 1},
                )

        self.stdout.write(self.style.SUCCESS(
            f"Released {released} seat(s); deleted {people_count} person record(s) "
            f"({deleted} row(s) in total)."))
        if not options["keep_counter"]:
            self.stdout.write(f"Next detail code will be {DETAIL_CODE_START}.")
