from pathlib import Path

p = Path(r"c:\personal\work\ftworkflow\people\views.py")
new = Path(r"c:\personal\work\ftworkflow\_snip_shift.py").read_text(encoding="utf-8")
if not new.endswith("\n\n"):
    new = new.rstrip() + "\n\n"
text = p.read_text(encoding="utf-8")
start = text.index("@login_required\n@admin_required\ndef person_shift(")
end = text.index("@login_required\n@require_POST\ndef shift_presence_ping(")
p.write_text(text[:start] + new + text[end:], encoding="utf-8")
print("rewrote", end - start, "chars ->", len(new))
