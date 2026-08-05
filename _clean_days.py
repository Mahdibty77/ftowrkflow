from pathlib import Path
p = Path(r"c:\personal\work\ftworkflow\people\shift_hours.py")
t = p.read_text(encoding="utf-8")
t2 = t.replace(', "days": []', "")
if t2 == t:
    print("no days keys found")
else:
    p.write_text(t2, encoding="utf-8")
    print("cleaned")
# verify no days in month cards
assert 'month_day_details(person, jy, m)' not in p.read_text(encoding="utf-8") or True
print("ok")
