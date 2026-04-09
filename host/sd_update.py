#!/usr/bin/env python3
"""
sd_update.py — Incremental update of the SD blob.

Rewrites only the slots you pass on the command line (default: initrd),
plus the header sector. Takes ~1 s vs ~166 s for a full sd_pack.py run.

Before writing, reads the on-card header via stage2 mode 'R' and
verifies magic / layout_version / LBA constants match sd_layout.py.
If not, aborts and tells you to run sd_pack.py first.

Usage:
  python3 host/sd_update.py                   # initrd only (default)
  python3 host/sd_update.py --dtb             # dtb + initrd
  python3 host/sd_update.py --kernel          # kernel (+ dtb + initrd? no — just kernel)

Flags are additive. The header is always rewritten with current sizes.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sd_layout as L
from sd_proto import (get_session, multi_seg_write, read_header,
                      BAUD_CANDIDATES)


def update_sd(ser, base, *, do_kernel=False, do_dtb=False, do_initrd=True,
              verify=False):
    """Run one update pass over an already-attached stage2 session.
    Caller owns `ser` (does not close it). Raises on layout mismatch or
    slot overflow. Returns (old_header, new_header) dicts."""
    kernel_path = os.path.join(base, "output", "Image")
    dtb_path    = os.path.join(base, "output", "neorv32_ax301.dtb")
    initrd_path = os.path.join(base, "output", "neo_initramfs.cpio.gz")

    with open(kernel_path, "rb") as f: image_d = f.read()
    with open(dtb_path,    "rb") as f: dtb_d   = f.read()
    with open(initrd_path, "rb") as f: ird_d   = f.read()

    for name, data_len, cap in [
        ("Image",  len(image_d), L.IMG_MAX_SEC),
        ("DTB",    len(dtb_d),   L.DTB_MAX_SEC),
        ("initrd", len(ird_d),   L.IRD_MAX_SEC),
    ]:
        used = (data_len + 511) // L.SECTOR
        if used > cap:
            raise RuntimeError(
                f"{name} ({used} sec) overflows slot ({cap}). "
                f"Bump sd_layout.py and re-run sd_pack.py.")

    # Step 1: verify on-card layout via mode 'R'
    hdr_bytes = read_header(ser)
    h = L.parse_header(hdr_bytes)
    L.verify_layout(h)  # raises ValueError on mismatch
    print(f"[✓] on-card layout v{h['layout_version']}  "
          f"img_lba={h['image_lba']} dtb_lba={h['dtb_lba']} "
          f"ird_lba={h['initrd_lba']}")
    print(f"    old sizes: img={h['image_sz']} dtb={h['dtb_sz']} "
          f"ird={h['initrd_sz']}")
    print(f"    new sizes: img={len(image_d)} dtb={len(dtb_d)} "
          f"ird={len(ird_d)}")

    new_header = L.build_header(len(image_d), len(dtb_d), len(ird_d))
    segments = [(L.HDR_LBA, new_header)]
    if do_kernel: segments.append((L.IMG_LBA, L.pad_sector(image_d)))
    if do_dtb:    segments.append((L.DTB_LBA, L.pad_sector(dtb_d)))
    if do_initrd: segments.append((L.IRD_LBA, L.pad_sector(ird_d)))

    total_sec = sum(len(d) // L.SECTOR for _, d in segments)
    print(f"[*] Updating {total_sec} sector(s) "
          f"({total_sec * 512 / 1024:.1f} KB)")
    multi_seg_write(ser, segments)

    expected = L.parse_header(new_header)
    if verify:
        print("[*] Verifying: re-reading header via mode 'R'")
        hdr2 = read_header(ser)
        got = L.parse_header(hdr2)
        mismatches = [k for k in ("image_sz", "dtb_sz", "initrd_sz",
                                  "image_lba", "dtb_lba", "initrd_lba",
                                  "layout_version")
                      if got[k] != expected[k]]
        if mismatches:
            raise RuntimeError(
                f"verify FAILED — fields differ: {mismatches} "
                f"got={ {k: got[k] for k in mismatches} } "
                f"expected={ {k: expected[k] for k in mismatches} }")
        print(f"[✓] verify OK  img={got['image_sz']} dtb={got['dtb_sz']} "
              f"ird={got['initrd_sz']}")
    return h, expected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--kernel", action="store_true",
                    help="also rewrite Image slot (rarely needed)")
    ap.add_argument("--dtb", action="store_true",
                    help="also rewrite DTB slot")
    ap.add_argument("--no-initrd", action="store_true",
                    help="skip initrd (e.g. only updating DTB)")
    ap.add_argument("--skip-program", action="store_true")
    ap.add_argument("--baud", type=int, default=BAUD_CANDIDATES[0],
                    help=f"target UART baud (default {BAUD_CANDIDATES[0]})")
    ap.add_argument("--persistent", action="store_true",
                    help="attach to already-running stage2 (skip program/upload)")
    ap.add_argument("--verify", action="store_true",
                    help="re-read header after write and compare sizes")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")

    do_initrd = not args.no_initrd
    if not (args.kernel or args.dtb or do_initrd):
        print("[!] nothing to update (use --kernel/--dtb or drop --no-initrd)")
        sys.exit(1)

    ser = get_session(base, args.port, stage2,
                      skip_program=args.skip_program,
                      target_baud=args.baud,
                      persistent=args.persistent)
    try:
        update_sd(ser, base,
                  do_kernel=args.kernel, do_dtb=args.dtb, do_initrd=do_initrd,
                  verify=args.verify)
    except ValueError as e:
        print(f"[!] layout check FAILED: {e}")
        print("[!] refusing to do a partial update — run sd_pack.py first.")
        ser.close(); sys.exit(2)
    except RuntimeError as e:
        print(f"[!] {e}"); ser.close(); sys.exit(1)
    ser.close()
    print("=== UPDATE OK ===")


if __name__ == "__main__":
    main()
