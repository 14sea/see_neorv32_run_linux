#!/usr/bin/env python3
"""
Upload a NEORV32 executable to the iCESugar board and run it.

The iCESugar's iCELink MCU bridges the FPGA UART to a USB CDC device, so the
port is /dev/ttyACM* here, NOT /dev/ttyUSB* like the AX301 board.

NEORV32 bootloader protocol (sw/bootloader/main.c):
    <any key>          abort the auto-boot countdown   -> "CMD:> "
    u                  -> "Awaiting neorv32_exe.bin... "
                          then stream the raw file, bootloader replies
                          "OK\\n" or "\\aERROR_<reason>\\n"
    e                  start the executable
    i                  print system info
    r                  restart the bootloader

Examples:
    python3 host/upload.py                       # build dir default, then run + terminal
    python3 host/upload.py --port /dev/ttyACM1
    python3 host/upload.py --info --no-run
    python3 host/upload.py --terminal            # just attach a terminal
"""

import argparse
import os
import sys
import termios
import threading
import time
import tty

import serial

DEFAULT_EXE = os.path.join(os.path.dirname(__file__), "..", "sw", "hello", "neorv32_exe.bin")
BOOTLOADER_BAUD = 19200  # neorv32/sw/bootloader/config.h: UART_BAUD


def read_until(ser, marker, timeout, label=""):
    """Read until `marker` appears; return everything received."""
    marker = marker.encode() if isinstance(marker, str) else marker
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            if marker in buf:
                return buf
    raise TimeoutError(
        f"timeout waiting for {label or marker!r} "
        f"(got {len(buf)} bytes: {buf[-160:]!r})"
    )


def enter_bootloader(ser, verbose=True):
    """Abort auto-boot and land on the 'CMD:>' prompt."""
    ser.reset_input_buffer()
    # A key press aborts the countdown. If the countdown already expired the
    # bootloader is sitting at the prompt anyway, and a stray byte just gets
    # echoed as an unknown command - harmless either way.
    for _ in range(3):
        ser.write(b"\r")
        ser.flush()
        try:
            out = read_until(ser, "CMD:>", 3.0, "CMD prompt")
            if verbose:
                sys.stdout.write(out.decode(errors="replace"))
                sys.stdout.flush()
            return
        except TimeoutError:
            continue
    raise TimeoutError(
        "no 'CMD:>' prompt. Is the FPGA programmed and is this the right port? "
        "Try pressing the board's reset button."
    )


def upload(ser, path, chunk=64, pace=0.0):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 16:
        sys.exit(f"error: {path} is only {len(data)} bytes - not a neorv32_exe.bin")

    print(f"[*] uploading {os.path.basename(path)} ({len(data)} bytes) "
          f"@ {ser.baudrate} baud ~{len(data)*10/ser.baudrate:.1f}s")

    ser.write(b"u")
    ser.flush()
    read_until(ser, "Awaiting", 5.0, "'Awaiting' prompt")

    sent = 0
    for i in range(0, len(data), chunk):
        ser.write(data[i:i + chunk])
        ser.flush()
        sent += len(data[i:i + chunk])
        if pace:
            time.sleep(pace)
        pct = 100 * sent // len(data)
        print(f"\r    {sent}/{len(data)} bytes ({pct}%)", end="", flush=True)
    print()

    out = read_until(ser, b"\n", 20.0, "upload result").decode(errors="replace")
    if "OK" in out:
        print("[+] upload OK")
        return True
    print(f"[!] bootloader rejected the image: {out.strip()}")
    return False


def terminal(ser):
    """Bidirectional terminal. Ctrl-] quits."""
    print("[*] terminal attached - press Ctrl-] to quit\n")
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            data = ser.read(256)
            if data:
                sys.stdout.write(data.decode(errors="replace"))
                sys.stdout.flush()

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    fd = sys.stdin.fileno()
    isatty = os.isatty(fd)
    old = termios.tcgetattr(fd) if isatty else None
    try:
        if isatty:
            tty.setraw(fd)
        while True:
            ch = os.read(fd, 1)
            if not ch or ch == b"\x1d":  # Ctrl-]
                break
            ser.write(ch)
            ser.flush()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if isatty:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\n[*] terminal detached")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0",
                    help="serial port of the iCELink CDC bridge (default: %(default)s)")
    ap.add_argument("--baud", type=int, default=BOOTLOADER_BAUD,
                    help="baud rate (default: %(default)s, the bootloader's)")
    ap.add_argument("--exe", default=DEFAULT_EXE,
                    help="neorv32_exe.bin to upload (default: sw/hello/)")
    ap.add_argument("--info", action="store_true",
                    help="print the bootloader's system info before uploading")
    ap.add_argument("--no-run", action="store_true",
                    help="upload but do not start the executable")
    ap.add_argument("--no-terminal", action="store_true",
                    help="do not attach a terminal afterwards")
    ap.add_argument("--terminal", action="store_true",
                    help="skip everything, just attach a terminal")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds to sleep between chunks (only if uploads fail)")
    args = ap.parse_args()

    if not os.path.exists(args.port):
        sys.exit(f"error: {args.port} does not exist.\n"
                 f"       On WSL2 attach the board first: "
                 f"usbipd attach --wsl --busid <ID>  (from Windows, as admin)")

    with serial.Serial(args.port, args.baud, timeout=0.1) as ser:
        if args.terminal:
            terminal(ser)
            return

        enter_bootloader(ser)

        if args.info:
            ser.write(b"i")
            ser.flush()
            print(read_until(ser, "CMD:>", 5.0, "info").decode(errors="replace"))

        if not upload(ser, args.exe, pace=args.pace):
            sys.exit(1)

        if not args.no_run:
            print("[*] starting executable ('e')")
            ser.write(b"e")
            ser.flush()

        if not args.no_terminal:
            terminal(ser)


if __name__ == "__main__":
    main()
