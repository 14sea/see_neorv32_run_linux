#!/usr/bin/env python3
"""
sd_layout.py — Shared SD blob layout constants for sd_pack.py and
sd_update.py. DO NOT change these without bumping LAYOUT_VERSION and
reflashing the full blob with sd_pack.py first.

Layout (512-byte sectors):
  LBA 0            : header (magic + sizes + LBAs + layout_version)
  LBA 1..4000      : Image  reserved slot (2 MB max)
  LBA 4001..4008   : DTB    reserved slot (4 KB max)
  LBA 4009..8008   : initrd reserved slot (2 MB max)

Fixed LBAs mean `sd_update.py` can rewrite just the initrd slot (and
the header) without shifting anything when the kernel grows.
"""
import struct

SECTOR        = 512
MAGIC         = b"NEOLNX\x00\x00"
LAYOUT_VERSION = 1

HDR_LBA       = 0

IMG_LBA       = 1
IMG_MAX_SEC   = 4000     # 2 MB

DTB_LBA       = IMG_LBA + IMG_MAX_SEC       # 4001
DTB_MAX_SEC   = 8        # 4 KB

IRD_LBA       = DTB_LBA + DTB_MAX_SEC       # 4009
IRD_MAX_SEC   = 4000     # 2 MB

END_LBA       = IRD_LBA + IRD_MAX_SEC       # 8009


def pad_sector(b: bytes) -> bytes:
    r = len(b) % SECTOR
    return b + b"\x00" * ((SECTOR - r) if r else 0)


def build_header(image_sz: int, dtb_sz: int, initrd_sz: int) -> bytes:
    """Build the 512-byte LBA-0 header sector."""
    hdr = MAGIC + struct.pack(
        "<IIIIIIIIII",
        image_sz, dtb_sz, initrd_sz,
        IMG_LBA, DTB_LBA, IRD_LBA,
        LAYOUT_VERSION,
        IMG_MAX_SEC, DTB_MAX_SEC, IRD_MAX_SEC,
    )
    return pad_sector(hdr)


def parse_header(buf: bytes) -> dict:
    """Parse a 512-byte header sector. Raises ValueError if malformed."""
    if len(buf) < SECTOR:
        raise ValueError(f"header too short: {len(buf)}")
    if buf[:8] != MAGIC:
        raise ValueError(f"bad magic: {buf[:8]!r}")
    fields = struct.unpack("<IIIIIIIIII", buf[8:8 + 40])
    return {
        "image_sz":     fields[0],
        "dtb_sz":       fields[1],
        "initrd_sz":    fields[2],
        "image_lba":    fields[3],
        "dtb_lba":      fields[4],
        "initrd_lba":   fields[5],
        "layout_version": fields[6],
        "image_max_sec":  fields[7],
        "dtb_max_sec":    fields[8],
        "initrd_max_sec": fields[9],
    }


def verify_layout(h: dict) -> None:
    """Assert the header matches the current layout constants. Raise
    ValueError with a user-friendly message if not."""
    if h["layout_version"] != LAYOUT_VERSION:
        raise ValueError(
            f"layout_version mismatch: card={h['layout_version']} "
            f"expected={LAYOUT_VERSION}. Run sd_pack.py first.")
    expected = {
        "image_lba":      IMG_LBA,
        "dtb_lba":        DTB_LBA,
        "initrd_lba":     IRD_LBA,
        "image_max_sec":  IMG_MAX_SEC,
        "dtb_max_sec":    DTB_MAX_SEC,
        "initrd_max_sec": IRD_MAX_SEC,
    }
    for k, v in expected.items():
        if h[k] != v:
            raise ValueError(
                f"{k} mismatch: card={h[k]} expected={v}. "
                f"Run sd_pack.py first.")
