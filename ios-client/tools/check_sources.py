#!/usr/bin/env python3
"""What can be checked with no Swift compiler. This is NOT a compile: it catches broken structure and references, and misses almost all type errors. See ../README.md.
  - Swift: brackets and strings balance
  - Types and functions the app and extension use from KiwiCore exist there, and are public
  - The Xcode project parses, every reference in it resolves, and every Swift file on disk is in it
  - Plists and JSON parse; entitlements and Info.plist agree on keychain groups
  - The private-key rule: nothing but the Keychain code and tests touches a key
"""
import json
import plistlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
problems: list[str] = []


def fail(message: str) -> None:
    problems.append(message)


def strip(text: str) -> str:
    """Comments and strings blanked, keeping `\\(...)` interpolation balanced by dropping it with the string."""
    out, i, n = [], 0, len(text)
    while i < n:
        two, c = text[i:i + 2], text[i]
        if two == "//":
            while i < n and text[i] != "\n":
                i += 1
        elif two == "/*":
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated comment")
            i = end + 2
        elif text[i:i + 3] == '"""':
            end = text.find('"""', i + 3)
            if end < 0:
                raise ValueError("unterminated multi-line string")
            i = end + 3
        elif two == '#"':
            end = text.find('"#', i + 2)
            if end < 0:
                raise ValueError("unterminated raw string")
            i = end + 2
        elif c == '"':
            i, depth = i + 1, 0
            while i < n:
                if text[i] == "\n":
                    raise ValueError("string runs past the end of its line")
                if text[i] == "\\" and text[i + 1] == "(":
                    depth, i = depth + 1, i + 2
                    continue
                if text[i] == "\\":
                    i += 2
                    continue
                if depth:
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                elif text[i] == '"':
                    break
                i += 1
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


PAIRS = {")": "(", "]": "[", "}": "{"}
swift = sorted(ROOT.rglob("*.swift"))
if not swift:
    fail("No Swift files")
stripped: dict[Path, str] = {}
for path in swift:
    rel = path.relative_to(ROOT).as_posix()
    try:
        code = strip(path.read_text(encoding="utf-8"))
    except ValueError as e:
        fail(f"{rel}: {e}")
        continue
    stripped[path] = code
    stack: list[str] = []
    for c in code:
        if c in "([{":
            stack.append(c)
        elif c in PAIRS:
            if not stack or stack.pop() != PAIRS[c]:
                fail(f"{rel}: unbalanced {c}")
                break
    else:
        if stack:
            fail(f"{rel}: {len(stack)} bracket(s) left open")

# --- KiwiCore's public surface --------------------------------------------------------------------------------------
core = [p for p in stripped if "Packages/KiwiCore/Sources" in p.as_posix()]
public: set[str] = set()
for path in core:
    public |= set(re.findall(r"public\s+(?:final\s+)?(?:struct|class|enum|protocol|func|let|var|typealias)\s+(\w+)", stripped[path]))
    public |= set(re.findall(r"public\s+static\s+(?:let|func|var)\s+(\w+)", stripped[path]))
    public |= set(re.findall(r"@MainActor\s+public\s+final\s+class\s+(\w+)", stripped[path]))
NEEDED = {"KiwiVPN": ["ConnectionCoordinator", "ConnectionState", "AccountStatus", "AdvancedSettings", "TunnelProfile", "TunnelStats", "TunnelStatus", "TunnelDriver", "KeychainStore", "Provisioner", "HTTPKiwiAPI", "SecretStore", "ApiError", "SignInAttempt",
                     "readSchemeCallback", "readLoopbackCallback", "loopbackReply", "signInCallbackScheme", "suggestedServer", "kiwiBaseURL", "Capability", "Passport", "Mission", "Server", "ServerRef", "Device", "Account", "KiwiAPI"],
          "KiwiTunnel": ["TunnelProfile", "TunnelStats", "wireGuardKeyItem"]}
for target, names in NEEDED.items():
    for name in names:
        if name not in public:
            fail(f"{target} uses {name}, and KiwiCore has no public {name}")
        if not any(re.search(rf"\b{name}\b", stripped[p]) for p in stripped if p.relative_to(ROOT).parts[0] == target):
            fail(f"{target} is listed as using {name}, and doesn't")
for path, code in stripped.items():
    parts = path.relative_to(ROOT).parts
    if parts[0] == "KiwiTunnel" and "import WireGuardKit" not in path.read_text(encoding="utf-8"):
        fail(f"{path.name}: the extension must use WireGuardKit")
    if parts[0] == "KiwiVPN" and re.search(r"^\s*import\s+WireGuardKit", path.read_text(encoding="utf-8"), re.M):
        fail(f"{path.name}: the app must not link WireGuard's Go library")

# --- the Xcode project ----------------------------------------------------------------------------------------------
pbx = ROOT / "KiwiVPN.xcodeproj/project.pbxproj"
if not pbx.exists():
    fail("No project.pbxproj: run generate_xcodeproj.py")
else:
    text = pbx.read_text(encoding="utf-8")
    if not text.startswith("// !$*UTF8*$!\n"):
        fail("project.pbxproj has the wrong header")
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[{}();=,]|[^\s{}();=,"]+', text.split("\n", 1)[1])
    pos = 0

    def parse():
        global pos
        t = tokens[pos]
        pos += 1
        if t == "{":
            out = {}
            while tokens[pos] != "}":
                key = parse()
                if tokens[pos] != "=":
                    raise ValueError(f"expected = after {key}")
                pos += 1
                out[key] = parse()
                if tokens[pos] != ";":
                    raise ValueError(f"expected ; after {key}")
                pos += 1
            pos += 1
            return out
        if t == "(":
            out = []
            while tokens[pos] != ")":
                out.append(parse())
                if tokens[pos] != ",":
                    raise ValueError("expected , in a list")
                pos += 1
            pos += 1
            return out
        return t[1:-1] if t.startswith('"') else t

    try:
        doc = parse()
        if pos != len(tokens):
            fail("project.pbxproj has text after its end")
        objects = doc["objects"]
        if doc["rootObject"] not in objects:
            fail("rootObject isn't an object")

        def walk(value, owner):
            if isinstance(value, dict):
                for v in value.values():
                    walk(v, owner)
            elif isinstance(value, list):
                for v in value:
                    walk(v, owner)
            elif isinstance(value, str) and re.fullmatch(r"[0-9A-F]{24}", value) and value not in objects:
                fail(f"{owner} refers to {value}, which doesn't exist")

        for key, item in objects.items():
            walk(item, key)
        names = {o["path"] for o in objects.values() if o["isa"] == "PBXFileReference"}
        built = sum(len(o["files"]) for o in objects.values() if o["isa"] == "PBXSourcesBuildPhase")
        app_swift = [p for p in swift if p.relative_to(ROOT).parts[0] in ("KiwiVPN", "KiwiTunnel")]
        for p in app_swift:
            if p.name not in names:
                fail(f"{p.name} isn't in the Xcode project: run generate_xcodeproj.py")
        if built != len(app_swift):
            fail(f"{built} files in the compile phases, and {len(app_swift)} on disk: run generate_xcodeproj.py")
        for name in ("WireGuardGoBridgeiOS", "KiwiTunnel", "KiwiVPN"):
            if not any(o.get("name") == name and "Target" in o["isa"] for o in objects.values()):
                fail(f"No {name} target")
    except (ValueError, IndexError, KeyError) as e:
        fail(f"project.pbxproj doesn't parse: {e}")

# --- plists, JSON and keychain groups -------------------------------------------------------------------------------
plists = {}
for path in [*ROOT.rglob("*.plist"), *ROOT.rglob("*.entitlements")]:
    try:
        plists[path.relative_to(ROOT).as_posix()] = plistlib.loads(path.read_bytes())
    except Exception as e:  # plistlib raises several kinds
        fail(f"{path.name} doesn't parse: {e}")
for path in ROOT.rglob("*.json"):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        fail(f"{path.name} doesn't parse: {e}")
for path in [p for p in ROOT.rglob("Contents.json") if p.parent.suffix in (".imageset", ".appiconset")]:
    for image in json.loads(path.read_text(encoding="utf-8"))["images"]:
        if "filename" in image and not (path.parent / image["filename"]).exists():
            fail(f"{path.parent.name} names {image['filename']}, which isn't there")

entitled_app = plists.get("KiwiVPN/KiwiVPN.entitlements", {})
entitled_ext = plists.get("KiwiTunnel/KiwiTunnel.entitlements", {})
info_app = plists.get("KiwiVPN/Info.plist", {})
info_ext = plists.get("KiwiTunnel/Info.plist", {})
TUNNEL = ["packet-tunnel-provider"]
for name, e in (("app", entitled_app), ("extension", entitled_ext)):
    if e.get("com.apple.developer.networking.networkextension") != TUNNEL:
        fail(f"The {name} doesn't have the packet tunnel entitlement")
groups_app, groups_ext = entitled_app.get("keychain-access-groups", []), entitled_ext.get("keychain-access-groups", [])
if info_app.get("KiwiAppKeychainGroup") not in groups_app or info_app.get("KiwiSharedKeychainGroup") not in groups_app:
    fail("The app's Info.plist names a keychain group its entitlements don't allow")
if groups_ext != [info_ext.get("KiwiSharedKeychainGroup")]:
    fail("The extension may only have the shared keychain group, so it never has the session")
if info_ext.get("NSExtension", {}).get("NSExtensionPointIdentifier") != "com.apple.networkextension.packet-tunnel":
    fail("The extension's Info.plist has the wrong extension point")
if "NSAppTransportSecurity" in info_app:
    fail("The app must not relax App Transport Security")

# --- security rules that can be read from the source ----------------------------------------------------------------
for path, code in stripped.items():
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    if "Tests/" in rel:
        continue
    for bad in ("WKWebView", "UIWebView", "UIWebView"):
        if bad in code:
            fail(f"{rel}: no embedded web view may be used for signing in")
    if re.search(r"\b(print|NSLog|debugPrint)\(", code):
        fail(f"{rel}: only the Diag log and os.Logger, with no secrets")
    if "getRuntimeConfiguration" in code and "KiwiTunnel" not in rel:
        fail(f"{rel}: wireguard-go's runtime text holds the private key, and must stay in the extension")
    if ".passwordReference" in code or "PrivateKey =" in code:
        fail(f"{rel}: the private key must never go in the VPN configuration")

if problems:
    print("\n".join(f"PROBLEM  {p}" for p in problems))
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print(f"OK: {len(swift)} Swift files balanced, project references resolve, plists agree. This is not a compile.")
