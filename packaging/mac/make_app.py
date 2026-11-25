#!/usr/bin/env python3
"""Build pub2idml.app, a drag-and-drop droplet around the command-line tools.

macOS only (uses osacompile, codesign, iconutil). Usage:

    python3 packaging/mac/make_app.py --dist DIR --out pub2idml.app --version 0.3.0

DIR must hold the CLI build: pub2idml, pub2json and libs/ (as produced in CI).

SPDX-License-Identifier: MIT
"""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*args):
    print("+", " ".join(args))
    subprocess.run(args, check=True)


def make_icon(icns_path: str) -> bool:
    """Write the shared app icon (packaging/icon.py) as .icns. Needs Pillow."""
    try:
        from PIL import Image

        sys.path.insert(0, os.path.dirname(HERE))
        import icon
    except ImportError:
        print("Pillow not installed - keeping the default droplet icon")
        return False
    img = icon.draw(1024)

    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "icon.iconset")
        os.makedirs(iconset)
        for s in (16, 32, 128, 256, 512):
            img.resize((s, s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}.png"))
            img.resize((2 * s, 2 * s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}@2x.png"))
        run("iconutil", "-c", "icns", iconset, "-o", icns_path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True, help="folder with pub2idml, pub2json, libs/")
    ap.add_argument("--out", default="pub2idml.app")
    ap.add_argument("--version", default="0.0.0")
    a = ap.parse_args()

    for need in ("pub2idml", "pub2json", "libs"):
        if not os.path.exists(os.path.join(a.dist, need)):
            sys.exit(f"{a.dist} has no {need}")

    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    run("osacompile", "-o", a.out, os.path.join(HERE, "pub2idml.applescript"))

    contents = os.path.join(a.out, "Contents")
    resources = os.path.join(contents, "Resources")
    bindir = os.path.join(resources, "bin")
    os.makedirs(bindir)
    shutil.copy2(os.path.join(a.dist, "pub2idml"), bindir)
    shutil.copy2(os.path.join(a.dist, "pub2json"), bindir)
    shutil.copytree(os.path.join(a.dist, "libs"), os.path.join(bindir, "libs"), symlinks=True)
    for doc in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        src = os.path.join(a.dist, doc)
        if os.path.exists(src):
            shutil.copy2(src, resources)

    # Info.plist: identity, version, and the file types the icon accepts (.pub and folders)
    plist_path = os.path.join(contents, "Info.plist")
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    info.update(
        {
            "CFBundleIdentifier": "io.github.halvar20000.pub2idml",
            "CFBundleName": "pub2idml",
            "CFBundleDisplayName": "pub2idml",
            "CFBundleShortVersionString": a.version,
            "CFBundleVersion": a.version,
            "NSHumanReadableCopyright": "MIT License - https://github.com/halvar20000/pub2idml",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Microsoft Publisher Document",
                    "CFBundleTypeExtensions": ["pub", "PUB"],
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                },
                {
                    "CFBundleTypeName": "Folder",
                    "LSItemContentTypes": ["public.folder"],
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "None",
                },
            ],
        }
    )

    if make_icon(os.path.join(resources, "droplet.icns")):
        info["CFBundleIconFile"] = "droplet"
        # newer osacompile versions add an asset catalog whose icon would win over the .icns
        info.pop("CFBundleIconName", None)
        car = os.path.join(resources, "Assets.car")
        if os.path.exists(car):
            os.remove(car)

    with open(plist_path, "wb") as f:
        plistlib.dump(info, f)

    # sign inside-out (ad hoc): libraries, tools, then the app itself
    libs = os.path.join(bindir, "libs")
    for name in sorted(os.listdir(libs)):
        run("codesign", "--force", "--sign", "-", os.path.join(libs, name))
    run("codesign", "--force", "--sign", "-", os.path.join(bindir, "pub2json"))
    run("codesign", "--force", "--sign", "-", os.path.join(bindir, "pub2idml"))
    run("codesign", "--force", "--sign", "-", a.out)
    run("codesign", "--verify", "--strict", "--verbose=2", a.out)
    print(f"built {a.out}")


if __name__ == "__main__":
    main()
