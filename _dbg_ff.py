import os, sys, re, html as htmlmod
sys.path.insert(0, "/app")
os.chdir("/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ftworkflow.settings")
import django
django.setup()
from django.test import RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.middleware import MessageMiddleware
from itemcoder import bridge

u = get_user_model().objects.filter(is_superuser=True).first()
rf = RequestFactory()
req = rf.get("/tool/case/1/TO/", {"mode": "edit", "side": "INTERNAL"})
req.user = u
def _mw(r): return r
SessionMiddleware(_mw).process_request(req)
MessageMiddleware(_mw).process_request(req)
req.session.save()
html = bridge.tool_for_case(req, 1, "TO").content.decode("utf-8", errors="replace")
# raw Filled_Features inner HTML for row-0
m = re.search(r'id="row-0"[\s\S]*?data-col-name="Filled_Features"[^>]*>([\s\S]*?)</td>', html)
if m:
    raw = m.group(1)
    print("RAW FF len", len(raw))
    print(raw[:800])
    print("--- has br?", "<br" in raw.lower())
else:
    print("not found")
