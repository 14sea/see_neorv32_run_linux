#!/usr/bin/env python3
"""Test all shell commands on the running nommu Linux.

The NEORV32 UART has a 1-byte RX FIFO. When the kernel is busy with
debug output on TX, the shell process may not get scheduled in time to
drain RX, causing byte loss. We mitigate this by:
  - Waiting for each character's echo before sending the next
  - Retrying commands that produce garbled output
"""
import serial
import sys
import time

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUD = 115200
MAX_RETRIES = 3


def wait_for_echo(ser, ch, timeout=2.0):
    """Wait until we see the sent character echoed back."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        data = ser.read(ser.in_waiting or 1)
        if data and ch.encode() in data:
            return True
        time.sleep(0.01)
    return False


def send_cmd(ser, cmd, wait=10.0):
    """Send a command, wait for response + next prompt, return output lines."""
    # Drain stale data, wait for debug output to settle
    ser.reset_input_buffer()
    time.sleep(0.5)
    ser.reset_input_buffer()

    # Send command char by char, wait for echo of each
    for c in cmd:
        ser.write(c.encode())
        ser.flush()
        wait_for_echo(ser, c)

    # Send CR
    ser.write(b"\r")
    ser.flush()

    # Collect response until next prompt appears
    buf = b""
    t0 = time.time()
    while time.time() - t0 < wait:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.1)
        if len(buf) > len(cmd) + 10 and b"nommu# " in buf:
            time.sleep(0.5)
            buf += ser.read(ser.in_waiting or 0)
            break

    # Parse: find output between echo+CR and next prompt
    text = buf.decode("ascii", errors="replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output = []
    for line in lines[1:]:
        if "nommu# " in line:
            break
        stripped = line.strip()
        if stripped:
            output.append(stripped)
    return output


ser = serial.Serial(PORT, BAUD, timeout=1.0,
                    xonxoff=False, rtscts=False, dsrdtr=False)
ser.dtr = False
ser.rts = False
time.sleep(0.5)

# Get a fresh prompt
ser.reset_input_buffer()
ser.write(b"\r")
ser.flush()
time.sleep(3)
ser.read(ser.in_waiting or 0)

print(f"Connected to {PORT} at {BAUD}")
print("=" * 60)

passed = 0
total = 0
for cmd in ["help", "uname", "info"]:
    total += 1
    print(f"\n>>> {cmd}")
    print("-" * 40)

    # Retry if output looks wrong (garbled by debug output interleaving)
    lines = []
    for attempt in range(MAX_RETRIES):
        lines = send_cmd(ser, cmd)
        # Check for garbled output
        if lines and not any("unknown:" in l for l in lines):
            break
        if attempt < MAX_RETRIES - 1:
            print(f"  (retry {attempt + 1})")
            time.sleep(1)

    if lines and not any("unknown:" in l for l in lines):
        passed += 1
        for line in lines:
            print(f"  {line}")
        print("  [PASS]")
    else:
        print("  [FAIL] no output")

print()
print("=" * 60)
print(f"Results: {passed}/{total} passed")
ser.close()
