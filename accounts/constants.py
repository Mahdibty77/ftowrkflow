"""Organisational units and roles used across the platform.

There are exactly three units. Each unit may have any combination of a manager,
a supervisor and one or more experts. A unit can also exist with only some of
those roles populated (for example a Technical unit with only a manager and no
supervisor) - the user accounts are still defined, they are simply not assigned.
"""


class Unit:
    COMMERCIAL = "COMMERCIAL"
    TECHNICAL = "TECHNICAL"
    SUPPLY = "SUPPLY"

    CHOICES = [
        (COMMERCIAL, "Commercial"),
        (TECHNICAL, "Technical"),
        (SUPPLY, "Supply"),
    ]

    LABELS = dict(CHOICES)


class Role:
    MANAGER = "MANAGER"
    SUPERVISOR = "SUPERVISOR"
    EXPERT = "EXPERT"

    CHOICES = [
        (MANAGER, "Manager"),
        (SUPERVISOR, "Supervisor"),
        (EXPERT, "Expert"),
    ]

    LABELS = dict(CHOICES)


class SupplyKind:
    """Supply experts are split into Internal and External (managers/supervisors are not)."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"

    CHOICES = [
        (INTERNAL, "Internal Supply"),
        (EXTERNAL, "External Supply"),
    ]
    LABELS = dict(CHOICES)


class Gender:
    """Used only to choose the honorific (Mr./Ms.) shown before a signer's
    last name on exported documents — see accounts.models.Profile.honorific.
    """
    MALE = "MALE"
    FEMALE = "FEMALE"

    CHOICES = [
        (MALE, "Male"),
        (FEMALE, "Female"),
    ]
    LABELS = dict(CHOICES)

    HONORIFIC = {MALE: "Mr.", FEMALE: "Ms."}
