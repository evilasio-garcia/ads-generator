"""
Pure functions for selecting and ordering ad images based on SKU naming conventions.

Naming patterns:
- Simple: {SKU}-{SEQ}.{ext}
- Kit/combo: {SKU}[-]?CB{X}-{SEQ}.{ext}
"""

import re
from typing import Dict, List, Optional


def parse_image_filename(sku: str, filename: str) -> Optional[Dict]:
    """
    Classify a filename as simple image, kit image, or irrelevant.

    Returns dict with keys: type ("simple"|"kit"), kit_size (int|None),
    seq (int), filename (str). Returns None if the file doesn't match.
    """
    escaped_sku = re.escape(sku)

    # Try kit pattern first (more specific — contains CB)
    kit_pattern = rf"^{escaped_sku}-?CB(\d)-?(\d+)\.[^.]+$"
    m = re.match(kit_pattern, filename, re.IGNORECASE)
    if m:
        return {
            "type": "kit",
            "kit_size": int(m.group(1)),
            "seq": int(m.group(2)),
            "filename": filename,
        }

    # Try simple pattern
    simple_pattern = rf"^{escaped_sku}-?(\d+)\.[^.]+$"
    m = re.match(simple_pattern, filename, re.IGNORECASE)
    if m:
        return {
            "type": "simple",
            "kit_size": None,
            "seq": int(m.group(1)),
            "filename": filename,
        }

    return None


def select_ad_images(
    sku: str,
    ad_type: str,
    available_files: List[str],
    kit_size: Optional[int] = None,
) -> List[Dict]:
    """
    Select and order images for a Mercado Livre ad.

    Args:
        sku: Product SKU (e.g. "ABC-123"), used as-is.
        ad_type: "simple" or "kit".
        available_files: List of filenames in the Drive folder.
        kit_size: Required when ad_type == "kit". Number of items in the kit.

    Returns:
        List of dicts with keys: fileName, position (1-based), source ("simple"|"kit").
    """
    # Step 1: classify all files
    simple_images = {}   # seq -> filename (first occurrence wins)
    kit_images = {}      # seq -> filename (first occurrence wins)

    for fname in available_files:
        parsed = parse_image_filename(sku, fname)
        if parsed is None:
            continue

        if parsed["type"] == "simple":
            if parsed["seq"] not in simple_images:
                simple_images[parsed["seq"]] = fname
        elif parsed["type"] == "kit" and ad_type == "kit" and parsed["kit_size"] == kit_size:
            if parsed["seq"] not in kit_images:
                kit_images[parsed["seq"]] = fname

    # Step 2: build result based on ad type
    if ad_type == "simple":
        sorted_seqs = sorted(simple_images.keys())
        return [
            {"fileName": simple_images[seq], "position": i + 1, "source": "simple"}
            for i, seq in enumerate(sorted_seqs)
        ]

    # ad_type == "kit"
    # Merge: simple as base, kit overwrites + adds
    merged = {}
    for seq, fname in simple_images.items():
        merged[seq] = {"fileName": fname, "source": "simple"}
    for seq, fname in kit_images.items():
        merged[seq] = {"fileName": fname, "source": "kit"}

    sorted_seqs = sorted(merged.keys())
    return [
        {"fileName": merged[seq]["fileName"], "position": i + 1, "source": merged[seq]["source"]}
        for i, seq in enumerate(sorted_seqs)
    ]
