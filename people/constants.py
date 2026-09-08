"""Constants for the people directory.

Kept in their own module (rather than inline in models) so the rest of the app
— forms, views, templates, and the later assignment work — has one place to
import from, matching how the existing apps are laid out.
"""

from django.utils.translation import gettext_lazy as _


class PersonStatus:
    """Whether someone is currently with the organisation.

    There is no "deleted". A person who leaves is marked DEPARTED: their record
    and every document they ever signed stay exactly as they are. This is the
    same rule the platform already applies to user accounts ("cut off" rather
    than delete), applied one level down to the human rather than the login.
    """

    ACTIVE = "ACTIVE"
    DEPARTED = "DEPARTED"

    # gettext_lazy, not gettext: this list is built once at import time, long
    # before any request (and therefore any viewer's chosen language) exists.
    # A plain gettext() call would freeze whichever language happened to be
    # active at that moment for every viewer thereafter; gettext_lazy defers
    # the catalog lookup to render time, per request, which is what lets one
    # shared choices= list show the correct language to each viewer. Same
    # reasoning as cases/constants.py's own CHOICES lists, which explain it
    # at greater length.
    CHOICES = [
        (ACTIVE, _("Active")),
        (DEPARTED, _("Departed")),
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
