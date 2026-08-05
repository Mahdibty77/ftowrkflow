"""Seed a ready-to-explore demo dataset.

Creates the administrator, one user for every unit/role combination, a handful
of clients and expert codes, a demo case that already sits with the Technical
unit, and (unless ``--skip-codes``) loads the bundled sample pipe code table into
the itemcoder SQLite DB so Build TO works out of the box.

Run after migrating::

    python manage.py migrate
    python manage.py seed_demo
    python manage.py runserver

All demo accounts use simple passwords -- change them before any real use.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.constants import Role, Unit
from cases import services
from cases.constants import DocKind, OfferType
from cases.models import Client, ExpertCode

ADMIN_PASSWORD = "admin12345"
USER_PASSWORD = "pass12345"


class Command(BaseCommand):
    help = "Create demo users, clients, expert codes, a sample case and code data."

    def add_arguments(self, parser):
        parser.add_argument("--skip-codes", action="store_true",
                            help="Do not import the bundled sample pipe code table.")

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding demo data…"))

        admin = self._ensure_admin()
        users = self._ensure_users()
        self._ensure_expert_codes(users)
        clients = self._ensure_clients(admin, users)
        self._ensure_case(users, clients)

        if not options["skip_codes"]:
            self._import_codes()
        else:
            self.stdout.write("  Skipping code-table import (--skip-codes).")

        self.stdout.write(self.style.SUCCESS("\nDone. Sign in with one of:"))
        self.stdout.write(f"  admin / {ADMIN_PASSWORD}   (administrator)")
        for username in users:
            self.stdout.write(f"  {username} / {USER_PASSWORD}")

    # ------------------------------------------------------------------ admin
    def _ensure_admin(self) -> User:
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"first_name": "Site", "last_name": "Administrator",
                      "is_staff": True, "is_superuser": True},
        )
        if created:
            admin.set_password(ADMIN_PASSWORD)
            admin.save()
        # The post_save signal flags superusers as admins; make sure of it.
        admin.profile.is_admin = True
        admin.profile.save()
        self.stdout.write("  Administrator ready: admin")
        return admin

    # ------------------------------------------------------------------ users
    def _ensure_users(self) -> dict:
        from accounts.constants import SupplyKind
        plan = [
            # username, first, last, unit, role, code, supply_kind
            ("com_manager", "Commercial", "Manager", Unit.COMMERCIAL, Role.MANAGER, "101", ""),
            ("com_super", "Commercial", "Supervisor", Unit.COMMERCIAL, Role.SUPERVISOR, "102", ""),
            ("com_expert", "Commercial", "Expert", Unit.COMMERCIAL, Role.EXPERT, "103", ""),
            ("tech_manager", "Technical", "Manager", Unit.TECHNICAL, Role.MANAGER, "201", ""),
            ("tech_super", "Technical", "Supervisor", Unit.TECHNICAL, Role.SUPERVISOR, "202", ""),
            ("tech_expert", "Technical", "Expert", Unit.TECHNICAL, Role.EXPERT, "203", ""),
            ("sup_manager", "Supply", "Manager", Unit.SUPPLY, Role.MANAGER, "301", ""),
            ("sup_super", "Supply", "Supervisor", Unit.SUPPLY, Role.SUPERVISOR, "302", ""),
            ("sup_expert_in", "Supply", "Internal", Unit.SUPPLY, Role.EXPERT, "303", SupplyKind.INTERNAL),
            ("sup_expert_ex", "Supply", "External", Unit.SUPPLY, Role.EXPERT, "304", SupplyKind.EXTERNAL),
        ]
        out = {}
        for username, first, last, unit, role, code, supply_kind in plan:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first, "last_name": last, "is_staff": False},
            )
            if created:
                user.set_password(USER_PASSWORD)
                user.save()
            p = user.profile
            p.is_admin = False
            p.unit = unit
            p.role = role
            p.supply_kind = supply_kind
            p.internal_code = code
            p.org_title = f"{first} {last}"
            p.save()
            out[username] = user
        # A general manager: sees everything, manages nobody.
        gm, created = User.objects.get_or_create(
            username="gen_manager",
            defaults={"first_name": "General", "last_name": "Manager", "is_staff": False},
        )
        if created:
            gm.set_password(USER_PASSWORD)
            gm.save()
        gp = gm.profile
        gp.is_admin = False
        gp.is_general_manager = True
        gp.unit = ""
        gp.role = ""
        gp.internal_code = "001"
        gp.org_title = "General Manager"
        gp.save()
        out["gen_manager"] = gm
        self.stdout.write(f"  Users ready: {len(out)} unit/role accounts")
        return out

    def _ensure_expert_codes(self, users: dict) -> None:
        # The expert code printed in a document number comes from the creator's
        # linked ExpertCode, so wire them up for the commercial users.
        mapping = [
            ("102", "Commercial Manager", users["com_manager"]),
            ("103", "Commercial Supervisor", users["com_super"]),
            ("104", "Commercial Expert", users["com_expert"]),
        ]
        for code, name, user in mapping:
            ExpertCode.objects.get_or_create(
                code=code, defaults={"name": name, "user": user})
        self.stdout.write("  Expert codes ready")

    # ---------------------------------------------------------------- clients
    def _ensure_clients(self, admin: User, users: dict) -> list:
        names = ["Pars Petrochemical Co.", "Khuzestan Steel Co.", "Isfahan Refinery"]
        clients = []
        for name in names:
            client, created = Client.objects.get_or_create(
                name=name,
                defaults={"code": services.next_client_code(), "created_by": admin},
            )
            clients.append(client)
        # Let the commercial expert use the first client.
        if clients:
            clients[0].assigned_experts.add(users["com_expert"])
        self.stdout.write(f"  Clients ready: {len(clients)}")
        return clients

    # ------------------------------------------------------------------- case
    def _ensure_case(self, users: dict, clients: list) -> None:
        from cases.models import Case
        if Case.objects.exists():
            self.stdout.write("  A case already exists; not creating the demo case.")
            return
        rows = [
            {"description": "Pipe API 5L GR.B PSL1 SMLS", "size": '1/2"', "unit": "m", "quantity": "120"},
            {"description": "Pipe API 5L GR.B PSL1 SMLS", "size": '3/4"', "unit": "m", "quantity": "60"},
            {"description": "Elbow 90 LR A234 WPB", "size": '2"', "unit": "pcs", "quantity": "24"},
        ]
        case = services.create_case(
            creator=users["com_manager"],
            kind=DocKind.INDENT,
            offer_type=OfferType.TO_PI,
            client=clients[0],
            order_no="PO-DEMO-001",
            deadline=None,
            price_type="INTERNAL",
            client_commercial_expert="Mr. Client Sales",
            client_technical_expert="Eng. Client Tech",
            client_technical_phone="+98 21 1234 5678",
            rows=rows,
        )
        # Move it to Technical so the demo starts mid-flow.
        try:
            services.submit_to_technical(case, users["com_manager"], "Initial submission (demo).")
        except Exception:
            pass
        self.stdout.write(f"  Demo case created: {case.doc_no}")

        # A second case priced both Internally and Externally to show the split.
        from cases.constants import PriceType
        case2 = services.create_case(
            creator=users["com_expert"],
            kind=DocKind.INDENT,
            offer_type=OfferType.TO_PI,
            client=clients[0],
            order_no="PO-DEMO-002",
            deadline=None,
            price_type=PriceType.BOTH,
            client_commercial_expert="Ms. Client Buyer",
            client_technical_expert="Eng. Client Tech 2",
            rows=rows,
        )
        try:
            services.submit_to_technical(case2, users["com_expert"], "Both internal & external (demo).")
        except Exception:
            pass
        self.stdout.write(f"  Demo split case created: {case2.doc_no}")

    # ------------------------------------------------------------------ codes
    def _import_codes(self) -> None:
        from itemcoder.importer import import_code_table
        from itemcoder.resource_paths import csv_path

        path = csv_path("code_table", "pipe_coding_data.csv")
        self.stdout.write("  Importing sample pipe code table into SQLite (this can take a few seconds)…")
        try:
            summary = import_code_table("pipe", path, filename="pipe_coding_data.csv", replace=True)
            self.stdout.write(self.style.SUCCESS(
                f"    Imported {summary.get('rows', 0):,} pipe records → {summary.get('path', '')}"))
        except Exception as exc:  # pragma: no cover - defensive
            self.stdout.write(self.style.WARNING(f"    Code import skipped: {exc}"))
