#!/usr/bin/env python3
"""Writes KiwiVPN.xcodeproj/project.pbxproj from the files on disk, with stable IDs, so a Windows machine can keep the Xcode project correct. Re-run it after adding or removing a source file.
It writes the file only. It proves nothing about whether Xcode accepts it: check_sources.py checks its structure, and project.yml is the fallback if Xcode objects. See ../README.md.
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "live.pricechat.kiwivpn"
WG_URL = "https://git.zx2c4.com/wireguard-apple"
WG_REVISION = "2fec12a6e1f6e3460b6ee483aa00ad29cddadab1"  # tag 1.0.16-27
DEPLOYMENT = "17.0"
VERSION = "0.1.0"
BUILD = "1"

objects: dict[str, dict] = {}


def oid(*parts: str) -> str:
    return hashlib.sha1("/".join(parts).encode()).hexdigest()[:24].upper()


def add(key: str, isa: str, **fields) -> str:
    objects[key] = {"isa": isa, **fields}
    return key


def swift_files(folder: str) -> list[Path]:
    return sorted((ROOT / folder).rglob("*.swift"))


def file_ref(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    kind = {".swift": "sourcecode.swift", ".plist": "text.plist.xml", ".entitlements": "text.plist.entitlements", ".xcassets": "folder.assetcatalog"}[path.suffix]
    return add(oid("ref", rel), "PBXFileReference", lastKnownFileType=kind, path=path.name, sourceTree="<group>")


def group(key: str, children: list[str], path: str | None = None, name: str | None = None) -> str:
    fields = {"children": children, "sourceTree": "<group>"}
    if path:
        fields["path"] = path
    if name:
        fields["name"] = name
    return add(oid("group", key), "PBXGroup", **fields)


def tree(folder: str, extras: list[str]) -> str:
    """A group per folder, so the project looks like the disk."""
    top = ROOT / folder
    root_children = [file_ref(p) for p in sorted(top.glob("*.swift"))] + [file_ref(top / e) for e in extras]
    for sub in sorted(d for d in top.iterdir() if d.is_dir() and d.suffix != ".xcassets"):
        root_children.append(group(f"{folder}/{sub.name}", [file_ref(p) for p in sorted(sub.glob("*.swift"))], path=sub.name))
    return group(folder, root_children, path=folder)


def build_file(target: str, ref: str | None = None, product: str | None = None, attrs: dict | None = None) -> str:
    fields: dict = {}
    if ref:
        fields["fileRef"] = ref
    if product:
        fields["productRef"] = product
    if attrs:
        fields["settings"] = attrs
    return add(oid("build", target, ref or product or ""), "PBXBuildFile", **fields)


def sources(target: str, folder: str) -> str:
    files = [build_file(target, ref=oid("ref", p.relative_to(ROOT).as_posix())) for p in swift_files(folder)]
    return add(oid("sources", target), "PBXSourcesBuildPhase", buildActionMask="2147483647", files=files, runOnlyForDeploymentPostprocessing="0")


def frameworks(target: str, products: list[str]) -> str:
    files = [build_file(target, product=p) for p in products]
    return add(oid("frameworks", target), "PBXFrameworksBuildPhase", buildActionMask="2147483647", files=files, runOnlyForDeploymentPostprocessing="0")


def configs(key: str, debug: dict, release: dict) -> str:
    d = add(oid("cfg", key, "Debug"), "XCBuildConfiguration", buildSettings=debug, name="Debug")
    r = add(oid("cfg", key, "Release"), "XCBuildConfiguration", buildSettings=release, name="Release")
    return add(oid("cfglist", key), "XCConfigurationList", buildConfigurations=[d, r], defaultConfigurationIsVisible="0", defaultConfigurationName="Release")


def dependency(owner: str, target_key: str, name: str) -> str:
    proxy = add(oid("proxy", owner, name), "PBXContainerItemProxy", containerPortal=oid("project"), proxyType="1", remoteGlobalIDString=target_key, remoteInfo=name)
    return add(oid("dep", owner, name), "PBXTargetDependency", target=target_key, targetProxy=proxy)


# --- files --------------------------------------------------------------------------------------------------------
assets = ROOT / "KiwiVPN/Assets.xcassets"
app_group = tree("KiwiVPN", ["Info.plist", "KiwiVPN.entitlements", "Assets.xcassets"])
ext_group = tree("KiwiTunnel", ["Info.plist", "KiwiTunnel.entitlements"])
app_product = add(oid("product", "app"), "PBXFileReference", explicitFileType="wrapper.application", includeInIndex="0", path="KiwiVPN.app", sourceTree="BUILT_PRODUCTS_DIR")
ext_product = add(oid("product", "ext"), "PBXFileReference", explicitFileType="wrapper.app-extension", includeInIndex="0", path="KiwiTunnel.appex", sourceTree="BUILT_PRODUCTS_DIR")
products = group("Products", [app_product, ext_product], name="Products")
main_group = group("main", [app_group, ext_group, products])

# --- packages -----------------------------------------------------------------------------------------------------
wg_pkg = add(oid("pkg", "wireguard"), "XCRemoteSwiftPackageReference", repositoryURL=WG_URL, requirement={"kind": "revision", "revision": WG_REVISION})
local_pkg = add(oid("pkg", "kiwicore"), "XCLocalSwiftPackageReference", relativePath="Packages/KiwiCore")
kit = add(oid("product-dep", "WireGuardKit"), "XCSwiftPackageProductDependency", package=wg_pkg, productName="WireGuardKit")
core_app = add(oid("product-dep", "KiwiCore-app"), "XCSwiftPackageProductDependency", productName="KiwiCore")
core_ext = add(oid("product-dep", "KiwiCore-ext"), "XCSwiftPackageProductDependency", productName="KiwiCore")

# --- settings -----------------------------------------------------------------------------------------------------
common = {
    "ALWAYS_SEARCH_USER_PATHS": "NO", "CLANG_ENABLE_MODULES": "YES", "CLANG_ENABLE_OBJC_ARC": "YES", "COPY_PHASE_STRIP": "NO", "ENABLE_BITCODE": "NO", "ENABLE_STRICT_OBJC_MSGSEND": "YES",
    "IPHONEOS_DEPLOYMENT_TARGET": DEPLOYMENT, "SDKROOT": "iphoneos", "SWIFT_VERSION": "5.0", "TARGETED_DEVICE_FAMILY": "1", "MARKETING_VERSION": VERSION, "CURRENT_PROJECT_VERSION": BUILD,
    "VALIDATE_PRODUCT": "NO", "KIWI_COMMIT": "",
}
project_cfg = configs(
    "project",
    {**common, "DEBUG_INFORMATION_FORMAT": "dwarf", "ENABLE_TESTABILITY": "YES", "GCC_OPTIMIZATION_LEVEL": "0", "ONLY_ACTIVE_ARCH": "YES", "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG", "SWIFT_OPTIMIZATION_LEVEL": "-Onone"},
    {**common, "DEBUG_INFORMATION_FORMAT": "dwarf-with-dsym", "SWIFT_COMPILATION_MODE": "wholemodule", "SWIFT_OPTIMIZATION_LEVEL": "-O", "VALIDATE_PRODUCT": "YES"},
)
signing = {"CODE_SIGN_STYLE": "Automatic", "DEVELOPMENT_TEAM": ""}
app_settings = {
    **signing, "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon", "ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME": "AccentColor", "CODE_SIGN_ENTITLEMENTS": "KiwiVPN/KiwiVPN.entitlements", "GENERATE_INFOPLIST_FILE": "NO",
    "INFOPLIST_FILE": "KiwiVPN/Info.plist", "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks"], "PRODUCT_BUNDLE_IDENTIFIER": BUNDLE, "PRODUCT_NAME": "$(TARGET_NAME)",
}
ext_settings = {
    **signing, "CODE_SIGN_ENTITLEMENTS": "KiwiTunnel/KiwiTunnel.entitlements", "GENERATE_INFOPLIST_FILE": "NO", "INFOPLIST_FILE": "KiwiTunnel/Info.plist",
    "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks", "@executable_path/../../Frameworks"], "PRODUCT_BUNDLE_IDENTIFIER": f"{BUNDLE}.tunnel", "PRODUCT_NAME": "$(TARGET_NAME)", "SKIP_INSTALL": "YES",
}
go_settings = {"PRODUCT_NAME": "$(TARGET_NAME)", "SDKROOT": "iphoneos", "SUPPORTED_PLATFORMS": "iphoneos"}

# --- targets ------------------------------------------------------------------------------------------------------
GO = oid("target", "go")
EXT = oid("target", "ext")
APP = oid("target", "app")

add(GO, "PBXLegacyTarget", buildArgumentsString="$(ACTION)", buildConfigurationList=configs("go", go_settings, go_settings), buildPhases=[], buildToolPath="/usr/bin/make",
    buildWorkingDirectory="${BUILD_DIR%Build/*}SourcePackages/checkouts/wireguard-apple/Sources/WireGuardKitGo", dependencies=[], name="WireGuardGoBridgeiOS", passBuildSettingsInEnvironment="1", productName="WireGuardGoBridgeiOS")

add(EXT, "PBXNativeTarget", buildConfigurationList=configs("ext", ext_settings, ext_settings),
    buildPhases=[sources("ext", "KiwiTunnel"), frameworks("ext", [kit, core_ext]), add(oid("res", "ext"), "PBXResourcesBuildPhase", buildActionMask="2147483647", files=[], runOnlyForDeploymentPostprocessing="0")],
    buildRules=[], dependencies=[dependency("ext", GO, "WireGuardGoBridgeiOS")], name="KiwiTunnel", packageProductDependencies=[kit, core_ext], productName="KiwiTunnel", productReference=ext_product, productType="com.apple.product-type.app-extension")

embed = add(oid("embed", "app"), "PBXCopyFilesBuildPhase", buildActionMask="2147483647", dstPath="", dstSubfolderSpec="13",
            files=[build_file("embed", ref=ext_product, attrs={"ATTRIBUTES": ["RemoveHeadersOnCopy"]})], name="Embed Foundation Extensions", runOnlyForDeploymentPostprocessing="0")
resources = add(oid("res", "app"), "PBXResourcesBuildPhase", buildActionMask="2147483647", files=[build_file("app", ref=oid("ref", "KiwiVPN/Assets.xcassets"))], runOnlyForDeploymentPostprocessing="0")
add(APP, "PBXNativeTarget", buildConfigurationList=configs("app", app_settings, app_settings),
    buildPhases=[sources("app", "KiwiVPN"), frameworks("app", [core_app]), resources, embed],
    buildRules=[], dependencies=[dependency("app", EXT, "KiwiTunnel")], name="KiwiVPN", packageProductDependencies=[core_app], productName="KiwiVPN", productReference=app_product, productType="com.apple.product-type.application")

add(oid("project"), "PBXProject", attributes={"BuildIndependentTargetsInParallel": "1", "LastSwiftUpdateCheck": "1600", "LastUpgradeCheck": "1600"}, buildConfigurationList=project_cfg, compatibilityVersion="Xcode 15.0",
    developmentRegion="en", hasScannedForEncodings="0", knownRegions=["en", "Base"], mainGroup=main_group, packageReferences=[wg_pkg, local_pkg], productRefGroup=products, projectDirPath="", projectRoot="", targets=[APP, EXT, GO])


# --- writing ------------------------------------------------------------------------------------------------------
SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./")


def quoted(value: str) -> str:
    if value and all(c in SAFE for c in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write(value, indent: int) -> str:
    pad = "\t" * indent
    if isinstance(value, dict):
        inner = "".join(f"{pad}\t{quoted(k)} = {write(value[k], indent + 1)};\n" for k in value)
        return "{\n" + inner + pad + "}"
    if isinstance(value, list):
        inner = "".join(f"{pad}\t{write(v, indent + 1)},\n" for v in value)
        return "(\n" + inner + pad + ")"
    return quoted(str(value))


def isa_first(item: dict) -> dict:
    return {"isa": item["isa"], **{k: item[k] for k in sorted(item) if k != "isa"}}


doc = {"archiveVersion": "1", "classes": {}, "objectVersion": "60", "objects": {k: isa_first(objects[k]) for k in sorted(objects)}, "rootObject": oid("project")}
out = ROOT / "KiwiVPN.xcodeproj/project.pbxproj"
out.parent.mkdir(exist_ok=True)
out.write_text("// !$*UTF8*$!\n" + write(doc, 0) + "\n", encoding="utf-8", newline="\n")

scheme = f"""<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.7">
   <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES">
      <BuildActionEntries>
         <BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">
            <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{APP}" BuildableName="KiwiVPN.app" BlueprintName="KiwiVPN" ReferencedContainer="container:KiwiVPN.xcodeproj"/>
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
   <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.DebuggerFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="NO">
      <BuildableProductRunnable runnableDebuggingMode="0">
         <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{APP}" BuildableName="KiwiVPN.app" BlueprintName="KiwiVPN" ReferencedContainer="container:KiwiVPN.xcodeproj"/>
      </BuildableProductRunnable>
   </LaunchAction>
   <ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
"""
scheme_path = ROOT / "KiwiVPN.xcodeproj/xcshareddata/xcschemes/KiwiVPN.xcscheme"
scheme_path.parent.mkdir(parents=True, exist_ok=True)
scheme_path.write_text(scheme, encoding="utf-8", newline="\n")
print(f"{len(objects)} objects written to {out.relative_to(ROOT)}")
