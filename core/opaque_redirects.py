"""308 redirects from legacy integer case URLs to opaque ``oid`` URLs.

Uses HTTP 308 (Permanent Redirect) so POST bodies (e.g. tool save) keep their
method when an old integer bookmark/URL is hit.
"""
from __future__ import annotations

from django.http.response import HttpResponseRedirectBase
from django.urls import reverse


class _HttpResponsePermanentRedirect308(HttpResponseRedirectBase):
    status_code = 308


def permanent_named_redirect(url_name: str):
    """Build a view that permanently redirects to ``url_name`` with same kwargs.

    Preserves the query string. Used so old bookmarks like ``/cases/12/`` land
    on ``/cases/<opaque>/`` after the oid migration.
    """

    def view(request, **kwargs):
        target = reverse(url_name, kwargs=kwargs)
        qs = request.META.get("QUERY_STRING", "")
        if qs:
            target = f"{target}?{qs}"
        return _HttpResponsePermanentRedirect308(target)

    return view
