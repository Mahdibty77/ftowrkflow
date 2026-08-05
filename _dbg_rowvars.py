import os, sys, re, json, html as htmlmod
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
resp = bridge.tool_for_case(req, 1, "TO")
html = resp.content.decode("utf-8", errors="replace")

# Find first pipe row attributes via regex
for m in re.finditer(r'<tr[^>]*id="row-(\d+)"[^>]*>', html):
    start = m.start()
    chunk = html[start:start+2500]
    if 'data-group="pipe"' not in chunk and "data-group='pipe'" not in chunk:
        # try any group
        pass
    gm = re.search(r'data-group="([^"]*)"', chunk)
    tm = re.search(r'data-type="([^"]*)"', chunk)
    vm = re.search(r'data-vars="([^"]*)"', chunk)
    print("row", m.group(1), "group", gm.group(1) if gm else None, "type", tm.group(1) if tm else None)
    if vm:
        raw = htmlmod.unescape(vm.group(1))
        print(" raw data-vars[:200]=", raw[:200])
        try:
            parsed = json.loads(raw)
            print(" parsed type", type(parsed).__name__)
            if isinstance(parsed, str):
                parsed2 = json.loads(parsed)
                print(" double-encoded ->", type(parsed2).__name__, list(parsed2)[:10] if isinstance(parsed2, dict) else parsed2)
                parsed = parsed2
            if isinstance(parsed, dict):
                keys = list(parsed.keys())
                print(" keys", keys[:15], "n=", len(keys))
                for k in keys:
                    if "material" in k.lower() or k.startswith("size") or "phisic" in k.lower() or "sch" in k.lower():
                        print("  ", k, "=", repr(parsed[k])[:80])
        except Exception as e:
            print(" parse err", e)
    # Filled_Features cell
    fm = re.search(r'data-col-name="Filled_Features"[^>]*>(.*?)</td>', chunk, re.S)
    if not fm:
        # search further in row
        end = html.find('</tr>', start)
        rowhtml = html[start:end]
        fm = re.search(r'data-col-name="Filled_Features"[^>]*>(.*?)</td>', rowhtml, re.S)
    if fm:
        text = re.sub(r'<[^>]+>', ' ', fm.group(1))
        text = htmlmod.unescape(re.sub(r'\s+', ' ', text)).strip()
        print(" Filled_Features:", text[:250])
    else:
        print(" no Filled_Features in first chunk")
    print("---")
    if int(m.group(1)) >= 2:
        break
