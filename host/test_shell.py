#!/usr/bin/env python3
"""Test all shell commands on the running nommu Linux."""
import serial
import sys
import time

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUD = 115200

def send_cmd(ser, cmd, wait=3.0):
    """Send a command and collect response."""
    ser.reset_input_buffer()
    time.sleep(0.1)
    # Send command + newline
    ser.write((cmd + "\n").encode())
    ser.flush()

    # Collect response
    buf = b""
    t0 = time.time()
    while time.time() - t0 < wait:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
        # Stop if we see the next prompt
        if b"nommu# " in buf and buf.count(b"nommu# ") >= 1:
            # Wait a bit more for any trailing data
            time.sleep(0.3)
            buf += ser.read(ser.in_waiting or 0)
            break
    return buf.decode("ascii", errors="replace")

ser = serial.Serial(PORT, BAUD, timeout=0.5,
                    xonxoff=False, rtscts=False, dsrdtr=False)
ser.dtr = False
ser.rts = False

print(f"Connected to {PORT} at {BAUD}")
print("=" * 60)

# First, send empty line to get a fresh prompt
resp = send_cmd(ser, "", wait=2)
print(f"[initial prompt] {resp.strip()}")
print()

# Test each command
for cmd in ["help", "uname", "info"]:
    print(f">>> {cmd}")
    print("-" * 40)
    resp = send_cmd(ser, cmd, wait=5)
    # Clean up: remove echo and debug markers, extract useful output
    lines = resp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        # Skip lines that are just debug markers
        cleaned = line
        # Remove 'u' markers that appear at start
        while cleaned.startswith('u'):
            cleaned = cleaned[1:]
        if cleaned.strip():
            print(f"  {cleaned}")
    print()

print("=" * 60)
print("All commands tested!")
ser.close()
