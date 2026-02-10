#!/usr/bin/env python3
"""
Test Trace Packet Decoder
Verify that trace packets are being decoded correctly
"""

import struct

def decode_trace_packet(hex_str):
    """Decode a trace packet from hex string"""
    frame = bytes.fromhex(hex_str)
    
    print(f"Raw hex: {hex_str}")
    print(f"Length: {len(frame)} bytes")
    print()
    
    if len(frame) < 12:
        print("ERROR: Packet too short (< 12 bytes)")
        return
    
    code = frame[0]
    reserved = frame[1]
    path_len = frame[2]
    flags = frame[3]
    tag = struct.unpack('<i', frame[4:8])[0]
    auth_code = struct.unpack('<i', frame[8:12])[0]
    
    print(f"Code: 0x{code:02x} ({'TRACE' if code == 0x89 else 'UNKNOWN'})")
    print(f"Reserved: {reserved}")
    print(f"Path Length: {path_len}")
    print(f"Flags: 0x{flags:02x}")
    print(f"Tag: {tag}")
    print(f"Auth Code: {auth_code}")
    print()
    
    # Extract path hashes
    if len(frame) >= 12 + path_len:
        print("Path Hashes:")
        path_hashes = []
        for i in range(path_len):
            hash_byte = frame[12 + i]
            path_hashes.append(f"{hash_byte:02x}")
            print(f"  Hop {i}: {hash_byte:02x}")
        print(f"  Route: {' → '.join(path_hashes)}")
        print()
    else:
        print("ERROR: Not enough data for path hashes")
        return
    
    # Extract SNR values
    snr_start = 12 + path_len
    if len(frame) >= snr_start + (path_len + 1):
        print("Signal Quality (SNR):")
        path_snrs = []
        for i in range(path_len + 1):
            snr_byte = struct.unpack('b', bytes([frame[snr_start + i]]))[0]
            snr_db = snr_byte / 4.0
            path_snrs.append(snr_db)
            
            # Determine quality indicator
            if snr_db >= 10:
                indicator = "🟢 Excellent"
            elif snr_db >= 5:
                indicator = "🟡 Good"
            elif snr_db >= 0:
                indicator = "🟠 Fair"
            else:
                indicator = "🔴 Poor"
            
            hop_label = f"Hop {i}" if i < path_len else "Final"
            print(f"  {hop_label}: {snr_db:6.1f} dB  {indicator}")
        
        avg_snr = sum(path_snrs) / len(path_snrs)
        print(f"\n  Average SNR: {avg_snr:.1f} dB")
    else:
        print("ERROR: Not enough data for SNR values")
        print(f"Expected {snr_start + path_len + 1} bytes, got {len(frame)}")


if __name__ == "__main__":
    print("=" * 60)
    print("MeshCore Trace Packet Decoder Test")
    print("=" * 60)
    print()
    
    # Example trace packet from your Discord
    # You'll need to replace this with an actual packet from your logs
    print("Example 1: Sample trace packet")
    print("-" * 60)
    
    # This is a synthetic example - format:
    # 89 (code) + 00 (reserved) + 03 (path_len=3) + 00 (flags)
    # + tag(4 bytes) + auth(4 bytes) + hashes(3 bytes) + snrs(4 bytes)
    example1 = "89000300" + "01000000" + "00000000" + "a1b2c3" + "32281e14"
    decode_trace_packet(example1)
    
    print()
    print("=" * 60)
    print()
    
    print("Example 2: Your actual packet")
    print("-" * 60)
    print("The packet you showed: 89000100758e85e50000000094d004...")
    print()
    print("To test with your actual packet:")
    print("1. Look at the logs for a full trace packet hex")
    print("2. Replace the hex string in this script")
    print("3. Run the script to see the decoded output")
    print()
    print("The decoder expects:")
    print("  - Byte 0: 0x89 (trace code)")
    print("  - Byte 2: Path length N")
    print("  - Total length: 12 + N + (N+1) bytes minimum")
