"""Constants for the people directory.

Kept in their own module (rather than inline in models) so the rest of the app
— forms, views, templates, and the later assignment work — has one place to
import from, matching how the existing apps are laid out.
"""


class PersonStatus:
    """Whether someone is currently with the organisation.

    There is no "deleted". A person who leaves is marked DEPARTED: their record
    and every document they ever signed stay exactly as they are. This is the
    same rule the platform already applies to user accounts ("cut off" rather
    than delete), applied one level down to the human rather than the login.
    """

    ACTIVE = "ACTIVE"
    DEPARTED = "DEPARTED"

    CHOICES = [
        (ACTIVE, "Active"),
        (DEPARTED, "Departed"),
    ]
    LABELS = dict(CHOICES)


# Where detail codes start. Chosen by the business; the first person created
# gets exactly this number and every later one is the next integer up.
#
# This is a THIRD code, deliberately independent of the two person-codes the
# platform already has (the internal code on a user profile, and the expert
# code table). Those two must not be touched: the internal code is embedded in
# every case document number, so changing its shape would change document
# numbers that are already frozen onto issued paperwork.
DETAIL_CODE_START = 100000001

# The key of the single counter row that hands out detail codes.
DETAIL_CODE_COUNTER_KEY = "person_detail_code"
