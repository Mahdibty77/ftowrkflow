"""Two small helpers the spec-driven card template needs.

Both exist because the template walks a *list of field names* produced by the
spec rather than iterating the form directly — that is what lets one template
render any form described in spec.py. Django's template language cannot index
a form or a dict by a variable, so these do it.
"""
from django import template

register = template.Library()


@register.filter
def formfield(form, name):
    """The bound field called ``name``, or nothing if the form has no such field.

    Returning None rather than raising matters: a card may list a field that a
    particular screen chose to skip, and a missing input should leave a gap,
    not break the page.
    """
    try:
        return form[name]
    except KeyError:
        return None


@register.filter
def dictkey(mapping, key):
    """``mapping[key]``, or an empty list. Used for the row tables' initial data."""
    try:
        return mapping.get(key, [])
    except AttributeError:
        return []
