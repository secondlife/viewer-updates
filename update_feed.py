#!/usr/bin/env python3
"""
Update the Velopack release feed for a channel by fetching release artifacts
from a GitHub release and merging them into the feed directory.

Usage:
    python update_feed.py <github_release_tag> <feed_directory> [--base-url URL]

By default, asset URLs point to GitHub release downloads. Use --base-url to
point them at a custom hosting location (e.g., viewer-download.secondlife.com).

Examples:
    # Use GitHub release URLs (default, constructed from tag)
    python update_feed.py "Second_Life_Release#8bac2181-2026.1.1" "QA Test Builds"

    # Use explicit GitHub release URL (for downstream forks)
    python update_feed.py "Second_Life_Release#8bac2181-2026.1.1" "QA Test Builds" \\
        --github-url "https://github.com/myorg/myviewer/releases/download/v1.0.0"

    # Use custom base URL for asset hosting
    python update_feed.py "Second_Life_Release#8bac2181-2026.1.1" "QA Test Builds" \\
        --base-url "https://viewer-download.secondlife.com/Viewer_26"
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse


GITHUB_REPO = "secondlife/viewer"


def github_download_url(tag, filename):
    """Build a GitHub release download URL for the given tag and filename."""
    encoded_tag = urllib.parse.quote(tag, safe='')
    return f"https://github.com/{GITHUB_REPO}/releases/download/{encoded_tag}/{filename}"


def make_asset_url(base_url, github_url, tag, filename):
    """Build an asset URL using a custom base URL, explicit GitHub URL, or default GitHub releases."""
    if base_url:
        return base_url.rstrip('/') + '/' + filename
    if github_url:
        return github_url.rstrip('/') + '/' + filename
    return github_download_url(tag, filename)


def fetch_file(url, dest_path):
    """Download a file from a URL using curl, following redirects."""
    result = subprocess.run(
        ["curl", "-L", "-f", "-s", "-o", dest_path, url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    return os.path.exists(dest_path) and os.path.getsize(dest_path) > 0


def fetch_json(url):
    """Download and parse a JSON file from a URL."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not fetch_file(url, tmp_path):
            return None
        with open(tmp_path, 'r') as f:
            return json.load(f)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def fetch_text(url):
    """Download a text file from a URL, stripping any UTF-8 BOM."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not fetch_file(url, tmp_path):
            return None
        with open(tmp_path, 'r', encoding='utf-8-sig') as f:
            return f.read().strip()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def rewrite_releases_json(releases_json, tag, base_url, github_url):
    """Rewrite FileName fields in releases.*.json ({"Assets": [...]}) to full URLs."""
    for asset in releases_json.get("Assets", []):
        filename = asset.get("FileName", "")
        if filename and "://" not in filename:
            asset["FileName"] = make_asset_url(base_url, github_url, tag, filename)
    return releases_json


def rewrite_assets_json(assets_json, tag, base_url, github_url):
    """Rewrite RelativeFileName fields in assets.*.json (array) to full URLs."""
    for entry in assets_json:
        filename = entry.get("RelativeFileName", "")
        if filename and "://" not in filename:
            entry["RelativeFileName"] = make_asset_url(base_url, github_url, tag, filename)
    return assets_json


def merge_assets(existing_path, new_assets):
    """Merge new entries into an existing assets.*.json file (plain array).
    Deduplicates by RelativeFileName."""
    if os.path.exists(existing_path):
        with open(existing_path, 'r') as f:
            existing = json.load(f)
    else:
        existing = []

    existing_filenames = {e["RelativeFileName"] for e in existing}

    for entry in new_assets:
        if entry["RelativeFileName"] not in existing_filenames:
            existing.append(entry)
            print(f"  Added asset entry: {entry['RelativeFileName']}")
        else:
            print(f"  Skipping duplicate asset: {entry['RelativeFileName']}")

    return existing


def merge_releases(existing_path, new_releases):
    """Merge new asset entries into an existing releases.*.json file.
    Appends new versions, avoids duplicates by Version+Type."""
    if os.path.exists(existing_path):
        with open(existing_path, 'r') as f:
            existing = json.load(f)
    else:
        existing = {"Assets": []}

    existing_keys = {
        (a["Version"], a["Type"]) for a in existing["Assets"]
    }

    for asset in new_releases.get("Assets", []):
        key = (asset["Version"], asset["Type"])
        if key not in existing_keys:
            existing["Assets"].append(asset)
            print(f"  Added release: {asset['Version']} ({asset['Type']})")
        else:
            print(f"  Skipping duplicate: {asset['Version']} ({asset['Type']})")

    return existing


def append_releases_line(existing_path, new_line):
    """Append a RELEASES line if not already present."""
    existing_lines = []
    if os.path.exists(existing_path):
        with open(existing_path, 'r', encoding='utf-8-sig') as f:
            existing_lines = [l.strip() for l in f.readlines() if l.strip()]

    # Check for duplicate by filename (second field)
    new_parts = new_line.split()
    existing_filenames = {l.split()[1] for l in existing_lines if len(l.split()) >= 2}

    if len(new_parts) >= 2 and new_parts[1] not in existing_filenames:
        existing_lines.append(new_line)
        print(f"  Added RELEASES entry: {new_line}")
    else:
        print(f"  RELEASES entry already exists, skipping")

    return "\n".join(existing_lines) + "\n"


def process_platform(tag, feed_dir, platform, base_url, github_url, releases_suffix=""):
    """Process a single platform's feed files.

    platform: 'win' or 'osx'
    base_url: custom base URL for assets, or None for GitHub
    github_url: explicit GitHub release download URL, or None to construct from tag
    releases_suffix: suffix for RELEASES file ('' for win, '-osx' for osx)
    """
    print(f"\nProcessing {platform}...")

    # Fetch the release's JSON files from GitHub (always — this is the source)
    # releases.*.json has {"Assets": [...]} with SHA hashes — the version/integrity data
    # assets.*.json has [{RelativeFileName, Type}] — the downloadable file list
    releases_url = github_download_url(tag, f"releases.{platform}.json")
    assets_url = github_download_url(tag, f"assets.{platform}.json")
    releases_file_url = github_download_url(tag, f"RELEASES{releases_suffix}")

    new_releases = fetch_json(releases_url)
    if not new_releases:
        print(f"  No releases.{platform}.json found in release, skipping platform")
        return False

    new_assets = fetch_json(assets_url)
    if not new_assets:
        print(f"  No assets.{platform}.json found in release, skipping platform")
        return False

    new_releases_text = fetch_text(releases_file_url)
    if not new_releases_text:
        print(f"  No RELEASES{releases_suffix} found in release, skipping")
        new_releases_text = None

    # Rewrite filenames to full URLs (GitHub or custom base)
    new_releases = rewrite_releases_json(new_releases, tag, base_url, github_url)
    new_assets = rewrite_assets_json(new_assets, tag, base_url, github_url)

    # Merge releases (append new version entry to {"Assets": [...]})
    releases_path = os.path.join(feed_dir, f"releases.{platform}.json")
    merged_releases = merge_releases(releases_path, new_releases)
    with open(releases_path, 'w') as f:
        json.dump(merged_releases, f, indent=2)
    print(f"  Wrote {releases_path}")

    # Merge assets JSON (Velopack uses this to find downloadable files)
    assets_path = os.path.join(feed_dir, f"assets.{platform}.json")
    merged_assets = merge_assets(assets_path, new_assets)
    with open(assets_path, 'w') as f:
        json.dump(merged_assets, f, indent=2)
    print(f"  Wrote {assets_path}")

    # Append RELEASES line
    if new_releases_text:
        releases_file_path = os.path.join(feed_dir, f"RELEASES{releases_suffix}")
        for line in new_releases_text.splitlines():
            line = line.strip()
            if line:
                updated = append_releases_line(releases_file_path, line)
                with open(releases_file_path, 'w') as f:
                    f.write(updated)
        print(f"  Wrote {releases_file_path}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Update a Velopack release feed from a GitHub release.")
    parser.add_argument("tag", help="GitHub release tag (e.g., 'Second_Life_Release#8bac2181-2026.1.1')")
    parser.add_argument("feed_dir", help="Feed directory (relative to script or absolute)")
    parser.add_argument("--base-url", default=None,
                        help="Custom base URL for asset downloads. "
                             "If omitted, uses GitHub release URLs. "
                             "Example: https://viewer-download.secondlife.com/Viewer_26")
    parser.add_argument("--github-url", default=None,
                        help="GitHub release URL to use instead of constructing from tag/repo. "
                             "Example: https://github.com/myorg/myviewer/releases/download/mytag")
    args = parser.parse_args()

    tag = args.tag
    feed_dir = args.feed_dir
    base_url = args.base_url
    github_url = args.github_url

    # Resolve feed_dir relative to this script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(feed_dir):
        feed_dir = os.path.join(script_dir, feed_dir)

    if not os.path.isdir(feed_dir):
        print(f"Feed directory does not exist: {feed_dir}")
        sys.exit(1)

    print(f"Tag: {tag}")
    print(f"Feed directory: {feed_dir}")
    print(f"Asset base URL: {base_url or github_url or 'GitHub releases (default)'}")

    process_platform(tag, feed_dir, "win", base_url, github_url, releases_suffix="")
    process_platform(tag, feed_dir, "osx", base_url, github_url, releases_suffix="-osx")

    print("\nDone.")


if __name__ == "__main__":
    main()
