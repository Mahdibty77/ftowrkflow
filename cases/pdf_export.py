"""Professional A4-landscape PDF export for TO / PI.

Renders the print-tuned HTML templates (based on ``PI_form.html``), paginates
item rows by each row's own content height, then converts to PDF via
headless Chrome/Edge.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from django.template.loader import render_to_string

from .constants import FormKind
from .export_data import (
    VENDOR_NAME,
    build_export_rows,
    client_name_only,
    code_no_for,
    doc_no_export,
    export_columns,
    export_name_for,
    form_date_jalali,
    normalize_terms,
    order_no,
    pi_totals,
    vendor_last_name,
    vendor_title,
    vendor_signature_data_uri,
    vendor_stamp_data_uri,
)

# Approximate layout budget in mm (A4 landscape 297 × 210).
# Kept slightly conservative so printed rows never spill under the
# signature / footer cards.
_PAGE_H = 210.0
_HEADER_H = 25.5          # doc-head (~95px)
_INFO_H = 22.0            # info cards
_SIGN_H = 16.5            # compact approval strip
_FOOTER_H = 11.0          # footer strip + margin
_TABLE_PAD = 6.0          # table-zone vertical margins/padding
_THEAD_H = 8.0
_TOTALS_H = 16.0          # last-page totals panel (PI)
_SAFETY_MM = 8.0          # page inset (side/bottom pad) + stamp overlap slack
_PAGE_INSET_V_MM = 6.0    # document padding top+bottom (2.5mm + 3.5mm)
_MIN_ROW_H = 5.2
_LINE_H_MM = 3.15         # ~10px font × 1.15 line-height
_ROW_PAD_MM = 2.8         # top+bottom cell padding (~5px each side)

# Column width fractions of the table (must sum ≈ 1.0)
_PI_WIDTHS = {
    "client_no": 0.04, "item": 0.035, "code": 0.07, "desc_client": 0.11,
    "remark": 0.07, "desc_ftco": 0.11, "size": 0.045, "qty": 0.035, "unit": 0.035,
    "brand": 0.055, "time": 0.06, "unit_price": 0.09, "service_price": 0.09,
    "total_price": 0.09,
}
_TO_WIDTHS = {
    "client_no": 0.05, "item": 0.05, "code": 0.09, "desc_client": 0.16,
    "remark": 0.10, "desc_ftco": 0.16, "size": 0.07, "qty": 0.05, "unit": 0.05,
    "brand": 0.10, "time": 0.12,
}

_TABLE_WIDTH_MM = 265.0  # page width minus side margins
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _plain_cell_text(text) -> str:
    """Strip HTML / collapse whitespace for wrap estimation."""
    raw = str(text or "")
    raw = _BR_RE.sub("\n", raw)
    raw = _TAG_RE.sub("", raw)
    raw = (
        raw.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return raw


def _estimate_lines(text: str, width_mm: float, font_pt: float = 10.0) -> int:
    plain = _plain_cell_text(text)
    if not plain.strip():
        return 1
    # Average glyph width for Segoe UI / Helvetica at ~10pt.
    char_w = font_pt * 0.3528 * 0.46
    per_line = max(4, int(width_mm / char_w))
    lines = 0
    for para in plain.split("\n"):
        chunk = _WS_RE.sub(" ", para).strip()
        if not chunk:
            lines += 1
            continue
        lines += max(1, math.ceil(len(chunk) / per_line))
    return max(1, lines)


def _row_needed_height(row: dict, columns: list[tuple[str, str]], widths: dict) -> float:
    """Natural height for one item row from its widest wrapped cell."""
    lines = 1
    for _title, key in columns:
        frac = widths.get(key, 0.08)
        cell_text = row.get(key, "")
        if key == "desc_ftco" and str(row.get("_flag_label") or "").strip():
            cell_text = f"{cell_text} [{row.get('_flag_label')}]"
        lines = max(lines, _estimate_lines(cell_text, _TABLE_WIDTH_MM * frac))
    return max(_MIN_ROW_H, _ROW_PAD_MM + lines * _LINE_H_MM)


def _available_body_mm(*, last_page: bool, show_totals: bool) -> float:
    used = (
        _HEADER_H + _INFO_H + _SIGN_H + _FOOTER_H + _TABLE_PAD + _THEAD_H
        + _SAFETY_MM + _PAGE_INSET_V_MM
    )
    if last_page and show_totals:
        used += _TOTALS_H
    return max(18.0, _PAGE_H - used)


def _build_page_rows(
    chunk: list[dict],
    columns: list[tuple[str, str]],
    heights: list[float],
) -> list[dict]:
    cell_rows = []
    for row, height_mm in zip(chunk, heights):
        cells = []
        for _title, key in columns:
            text = str(row.get(key, "") or "")
            cells.append({
                "text": text,
                "left": key in {"desc_client", "desc_ftco", "remark"},
                "key": key,
                "flag_label": (str(row.get("_flag_label") or "") if key == "desc_ftco" else ""),
                "flag_kind": (
                    "issue" if key == "desc_ftco" and str(row.get("_issue", "") or "") == "1"
                    else "unsup" if key == "desc_ftco" and str(row.get("_unsuppliable", "") or "") == "1"
                    else "service" if key == "desc_ftco" and bool(str(row.get("_service_comment", "") or "").strip())
                         and str(row.get("_service_comment", "") or "").strip().lower() not in ("nan", "none", "<na>", "null")
                    else ""
                ),
            })
        cell_rows.append({
            "cells": cells,
            "height_mm": round(height_mm, 2),
            "issue": str(row.get("_issue", "") or "") == "1",
            "unsuppliable": str(row.get("_unsuppliable", "") or "") == "1",
            "service": bool(str(row.get("_service_comment", "") or "").strip())
                       and str(row.get("_service_comment", "") or "").strip().lower()
                       not in ("nan", "none", "<na>", "null"),
        })
    return cell_rows


def paginate_rows(rows: list[dict], columns: list[tuple[str, str]], *, is_pi: bool):
    """Pack item rows into pages by each row's own content height.

    Algorithm:
    1. Estimate a *natural* height for every row from its wrapped cell text
       (includes comfortable top/bottom cell padding so text is not flush
       against the row borders).
    2. Greedy-pack consecutive rows while
       ``sum(natural heights) ≤ available table-body height``.
    3. A tall row only expands itself while packing — short neighbours keep
       their own natural height.
    4. **Full pages** (more item rows continue on the next page): leftover
       space inside the table frame is distributed evenly across the rows on
       that page so the table card has no empty band at the bottom.
    5. **Partial last page**: rows keep natural heights; empty space under the
       last row is fine.
    6. A single row taller than the body still gets its own page (never dropped).
    """
    widths = _PI_WIDTHS if is_pi else _TO_WIDTHS
    if not rows:
        return [{
            "rows": [],
            "fill": False,
            "is_last_items": True,
            "show_totals": is_pi,
        }]

    needed = [_row_needed_height(r, columns, widths) for r in rows]
    pages: list[dict] = []
    i = 0
    n = len(rows)
    while i < n:
        # Always place at least the next row on this page.
        k = 1
        while i + k < n:
            trial = k + 1
            is_last_trial = (i + trial) >= n
            avail = _available_body_mm(
                last_page=is_last_trial,
                show_totals=bool(is_pi and is_last_trial),
            )
            trial_sum = sum(needed[i:i + trial])
            if trial_sum <= avail + 0.05:
                k = trial
            else:
                break

        chunk = rows[i:i + k]
        chunk_h = list(needed[i:i + k])
        is_last = (i + k) >= n
        avail = _available_body_mm(
            last_page=is_last,
            show_totals=bool(is_pi and is_last),
        )
        natural_sum = sum(chunk_h)

        # Full continuation pages: grow rows evenly into leftover body space
        # so the table frame is filled (no empty strip under the last row).
        # Partial last page keeps natural heights.
        fill = (not is_last) and len(chunk_h) > 0
        if fill and natural_sum < avail - 0.05:
            extra = (avail - natural_sum) / len(chunk_h)
            chunk_h = [h + extra for h in chunk_h]
        elif (not is_last) and natural_sum > avail + 0.05 and len(chunk_h) == 1:
            # Lone oversize row: clamp to body so it still fits the frame.
            chunk_h = [avail]

        pages.append({
            "rows": _build_page_rows(chunk, columns, chunk_h),
            "fill": fill,
            "is_last_items": is_last,
            "show_totals": bool(is_pi and is_last),
        })
        i += k
    return pages


def _chrome_path() -> str | None:
    """Locate a Chromium-based browser on the host or inside Docker."""
    env = (os.environ.get("CHROME_PATH") or os.environ.get("CHROMIUM_PATH") or "").strip()
    candidates: list[str] = []
    if env:
        candidates.append(env)

    # Linux / Docker (debian chromium package)
    candidates.extend([
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
    ])

    # Windows common install locations
    local_app = os.environ.get("LOCALAPPDATA") or ""
    prog = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
    prog86 = os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    candidates.extend([
        str(Path(prog) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(prog86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe") if local_app else "",
        str(Path(prog) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        str(Path(prog86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ])

    # PATH lookup
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "chrome", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # Windows registry (App Paths)
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
                ):
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            value, _ = winreg.QueryValueEx(key, None)
                            if value:
                                candidates.append(str(value))
                    except OSError:
                        continue
        except Exception:
            pass

    seen: set[str] = set()
    for path in candidates:
        path = (path or "").strip().strip('"')
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            if Path(path).is_file() and os.access(path, os.X_OK if os.name != "nt" else os.F_OK):
                return path
        except OSError:
            continue
    return None


# A4 landscape in inches (Chromium Page.printToPDF uses inches).
_A4_LANDSCAPE_W_IN = 297.0 / 25.4
_A4_LANDSCAPE_H_IN = 210.0 / 25.4


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get_json(url: str, timeout: float = 15.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ws_connect(ws_url: str, timeout: float = 20.0):
    """Minimal WebSocket client (text frames only) for Chrome DevTools Protocol."""
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise RuntimeError("CDP WebSocket handshake failed (connection closed)")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    if "101" not in status_line:
        sock.close()
        raise RuntimeError(f"CDP WebSocket handshake failed: {status_line}")
    return sock


def _ws_send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray()
    n = len(payload)
    header.append(0x81)  # FIN + text
    mask_bit = 0x80
    if n < 126:
        header.append(mask_bit | n)
    elif n < (1 << 16):
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + masked)


def _ws_recv_text(sock: socket.socket) -> str:
    def read_exact(n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("CDP WebSocket closed while reading")
            out += chunk
        return out

    while True:
        b1, b2 = read_exact(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", read_exact(8))[0]
        mask = read_exact(4) if masked else b""
        payload = read_exact(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            raise RuntimeError("CDP WebSocket closed by peer")
        if opcode == 0x9:  # ping → pong
            # echo pong
            hdr = bytearray([0x8A, 0x80 | len(payload)])
            m = os.urandom(4)
            hdr.extend(m)
            sock.sendall(bytes(hdr) + bytes(b ^ m[i % 4] for i, b in enumerate(payload)))
            continue
        if opcode in (0x1, 0x2):  # text / binary
            return payload.decode("utf-8")
        # ignore continuation/other


def _cdp_call(sock: socket.socket, state: dict, method: str, params: dict | None = None,
              *, timeout: float = 60.0):
    msg_id = int(state.get("id") or 1)
    state["id"] = msg_id + 1
    payload = {"id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    _ws_send_text(sock, json.dumps(payload))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock.settimeout(max(0.5, deadline - time.monotonic()))
        try:
            raw = _ws_recv_text(sock)
        except socket.timeout:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(f"CDP {method} error: {data['error']}")
            return data.get("result") or {}
    raise TimeoutError(f"CDP {method} timed out")


def _html_to_pdf_cdp(chrome: str, html_uri: str) -> bytes:
    """Print HTML to exact A4 landscape PDF via Chrome DevTools Protocol."""
    port = _free_port()
    user_data = tempfile.mkdtemp(prefix="ft_chrome_")
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data}",
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sock = None
    try:
        version = None
        for _ in range(50):
            try:
                version = _http_get_json(f"http://127.0.0.1:{port}/json/version")
                break
            except Exception:
                time.sleep(0.1)
        if not version:
            raise RuntimeError("Chromium DevTools endpoint did not start")

        # Open a dedicated page target for our file:// document.
        try:
            target = _http_get_json(
                f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(html_uri, safe='')}"
            )
        except Exception:
            # Older Chromium: PUT /json/new
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(html_uri, safe='')}",
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                target = json.loads(resp.read().decode("utf-8"))

        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("No CDP webSocketDebuggerUrl for print target")

        sock = _ws_connect(ws_url)
        state = {"id": 1}
        _cdp_call(sock, state, "Page.enable")
        # Wait until the document is fully loaded (file:// is usually instant).
        for _ in range(40):
            result = _cdp_call(sock, state, "Runtime.evaluate", {
                "expression": "document.readyState",
                "returnByValue": True,
            })
            ready = ((result.get("result") or {}).get("value")) or ""
            if ready == "complete":
                break
            time.sleep(0.05)
        # Give fonts/layout a brief settle.
        time.sleep(0.15)

        result = _cdp_call(sock, state, "Page.printToPDF", {
            "landscape": True,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True,
            "paperWidth": _A4_LANDSCAPE_W_IN,
            "paperHeight": _A4_LANDSCAPE_H_IN,
            "marginTop": 0,
            "marginBottom": 0,
            "marginLeft": 0,
            "marginRight": 0,
        }, timeout=120.0)
        data_b64 = result.get("data") or ""
        if not data_b64:
            raise RuntimeError("Page.printToPDF returned empty data")
        pdf = base64.b64decode(data_b64)
        if len(pdf) < 100:
            raise RuntimeError("Page.printToPDF returned truncated PDF")
        return pdf
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(user_data, ignore_errors=True)


def html_to_pdf(html: str) -> bytes:
    """Convert print HTML to A4-landscape PDF bytes via headless Chromium.

    Prefer Chrome DevTools ``Page.printToPDF`` so paper size is exactly A4
    landscape with zero margins and no browser header/footer. Fall back to the
    ``--print-to-pdf`` CLI flag when CDP is unavailable.
    """
    chrome = _chrome_path()
    if not chrome:
        raise RuntimeError(
            "Chrome/Edge/Chromium was not found. "
            "Inside Docker the image must include the chromium package; "
            "on Windows install Google Chrome or set CHROME_PATH."
        )

    with tempfile.TemporaryDirectory(prefix="ft_pdf_") as tmp:
        html_path = Path(tmp) / "document.html"
        pdf_path = Path(tmp) / "document.pdf"
        html_path.write_text(html, encoding="utf-8")
        uri = html_path.resolve().as_uri()

        try:
            return _html_to_pdf_cdp(chrome, uri)
        except Exception as cdp_err:
            # Fallback: CLI print (may use Letter on some builds).
            cmd = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-animations",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=8000",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path}",
                uri,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, check=False,
            )
            if not pdf_path.is_file() or pdf_path.stat().st_size < 100:
                err = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(
                    f"PDF generation failed (browser={chrome}; "
                    f"cdp={cdp_err}; cli={err or 'empty output'})"
                ) from cdp_err
            return pdf_path.read_bytes()


def build_document_context(case, form, terms: dict | None = None, *, pdf_lite: bool = False) -> dict:
    from .constants import Side
    from .constants import PriceType
    from .export_data import (
        form_currency, currency_export_suffix, technical_problem_export_rows,
        service_price_export_rows,
    )

    kind = (form.kind or "").upper()
    is_pi = kind == FormKind.PI
    columns = export_columns(form)
    rows = build_export_rows(case, form)
    pages = paginate_rows(rows, columns, is_pi=is_pi)
    issue_rows = technical_problem_export_rows(form) if kind == FormKind.TO else []
    has_issues_page = bool(issue_rows)
    service_rows = service_price_export_rows(form, case) if is_pi else []
    has_services_page = bool(service_rows)
    total_pages = len(pages) + (1 if has_issues_page else 0) + (1 if has_services_page else 0) + 1  # + issues? + services? + terms

    for idx, page in enumerate(pages, start=1):
        page["page_no"] = idx
        page["page_label"] = f"{idx} of {total_pages}"

    issues_page = None
    next_no = len(pages) + 1
    if has_issues_page:
        issues_page = {
            "page_no": next_no,
            "page_label": f"{next_no} of {total_pages}",
            "rows": issue_rows,
        }
        next_no += 1

    services_page = None
    if has_services_page:
        services_page = {
            "page_no": next_no,
            "page_label": f"{next_no} of {total_pages}",
            "rows": service_rows,
        }
        next_no += 1

    terms_page = {
        "page_no": next_no,
        "page_label": f"{next_no} of {total_pages}",
        "terms": normalize_terms(terms, kind=kind),
    }

    side = getattr(form, "side", "") or ""
    is_external = (
        side == Side.EXTERNAL
        or (not side and getattr(case, "price_type", "") == PriceType.EXTERNAL)
    )
    totals = None
    if is_pi:
        cur = form_currency(form, case)
        totals = pi_totals(rows, currency=currency_export_suffix(cur, external=is_external))

    return {
        "case": case,
        "form": form,
        "kind": kind,
        "is_pi": is_pi,
        "title": "PROFORMA INVOICE" if is_pi else "TECHNICAL OFFER",
        "kicker": "Engineering Commercial Document" if is_pi else "Engineering Technical Document",
        "footer_center": "PROFORMA INVOICE" if is_pi else "TECHNICAL OFFER",
        "vendor": VENDOR_NAME,
        "doc_no": doc_no_export(case, form),
        "client": client_name_only(case, form),
        "order_no": order_no(case, form) or "—",
        "code_no": code_no_for(form),
        "form_date": form_date_jalali(form),
        "columns": [
            {"title": t, "key": k, "width_pct": round((_PI_WIDTHS if is_pi else _TO_WIDTHS).get(k, 0.08) * 100, 2)}
            for t, k in columns
        ],
        "pages": pages,
        "issues_page": issues_page,
        "services_page": services_page,
        "terms_page": terms_page,
        "totals": totals,
        "currency_suffix": (totals or {}).get("currency_suffix", " IRR"),
        "vendor_name": vendor_last_name(form),
        # The seat the signer held when the document was frozen. Blank on
        # documents frozen before this was captured, which renders exactly
        # as they always have — the name alone.
        "vendor_title": vendor_title(form),
        "vendor_signature": vendor_signature_data_uri(form),
        "vendor_stamp": vendor_stamp_data_uri(form),
        "pdf_lite": pdf_lite,
    }


def render_document_html(case, form, terms: dict | None = None, *, print_toolbar: bool = False,
                         pdf_lite: bool = False) -> str:
    ctx = build_document_context(case, form, terms=terms, pdf_lite=pdf_lite)
    if print_toolbar:
        ctx["print_toolbar"] = True
        ctx["export_filename"] = export_name_for(case, form)
    return render_to_string("cases/export/document.html", ctx)


def render_print_view_html(case, form, terms: dict | None = None) -> tuple[str, str]:
    name = export_name_for(case, form)
    html = render_document_html(case, form, terms=terms, print_toolbar=True, pdf_lite=False)
    return html, name + ".html"


def render_form_pdf(case, form, terms: dict | None = None) -> tuple[bytes, str]:
    html = render_document_html(case, form, terms=terms, pdf_lite=True)
    pdf = html_to_pdf(html)
    return pdf, export_name_for(case, form) + ".pdf"
