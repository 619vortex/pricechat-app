"""AUDIT (2026-11 security review): list every API route, whether admin_guard treats it as public, and which
auth markers its endpoint source contains. Diagnostic only; run as a script, not collected by pytest."""
import inspect, os, re, sys
os.environ.setdefault("ENVIRONMENT", "development")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import create_app
from app.security import admin_auth
from app.pokemon import public as market_public

MARKERS = {
    "member": re.compile(r"_require\(|_current\(|require_member|current_member|_member\(|pc_session|member_from|_signed_in|_me\("),
    "bearer": re.compile(r"[Aa]uthorization|bearer|token_ok|_worker_auth|check_token|verify_token|x-worker|X-Worker|_auth\("),
    "sig": re.compile(r"signature|hmac|verify_sig|secret_token|X-Telegram-Bot-Api-Secret", re.I),
    "owner": re.compile(r"admin_auth\.current|_admin_ok|is_owner|owner_only|_owner\("),
}
app = create_app(initialize_database=False, background_jobs=False)
def walk(routes, prefix=""):
    for r in routes:
        if hasattr(r, "original_router"):
            yield from walk(r.original_router.routes, prefix + r.include_context.prefix)
        else:
            yield prefix, r


rows = []
for prefix, r in walk(app.routes):
    path = prefix + getattr(r, "path", "")
    if not path.startswith("/api/"):
        continue
    for m in sorted(getattr(r, "methods", []) or []):
        if m == "HEAD": continue
        sample = re.sub(r"\{[^}]+\}", "1", path)
        public = admin_auth.is_public(m, sample)
        mpub = (not public) and market_public.is_public_get(m, sample)
        try:
            src = inspect.getsource(r.endpoint)
        except Exception:
            src = ""
        params = list(inspect.signature(r.endpoint).parameters)
        marks = [k for k, rx in MARKERS.items() if rx.search(src) or (k == "member" and "pc_session" in params)]
        rows.append((("PUBLIC" if public else "MARKETPUB" if mpub else "ADMIN"), m, path, r.endpoint.__module__.split(".")[-1] + "." + r.endpoint.__name__, ",".join(marks) or "-"))
for row in sorted(rows, key=lambda x: (x[0], x[2])):
    print("\t".join(row))
print("TOTAL", len(rows), "public", sum(1 for x in rows if x[0] == "PUBLIC"), "marketpub", sum(1 for x in rows if x[0] == "MARKETPUB"), file=sys.stderr)
