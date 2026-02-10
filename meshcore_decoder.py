"""
MeshCore Frame Decoder
Parses MeshCore frames and emits structured events
"""

import struct
import logging
from datetime import datetime
from typing import Dict, Optional, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Types of events emitted by decoder"""
    CHANNEL_MESSAGE = "channel_message"
    DIRECT_MESSAGE = "direct_message"
    MESH_PACKET = "mesh_packet"
    ADVERTISEMENT = "advertisement"
    CONTACT = "contact"
    ACK = "ack"
    RAW_DATA = "raw_data"
    TRACE = "trace"
    MESSAGE_WAITING = "message_waiting"
    BRIDGE_CONNECTED = "bridge_connected"
    CONTACT_SUMMARY = "contact_summary"


@dataclass
class MeshEvent:
    """Structured event from decoder"""
    type: EventType
    timestamp: datetime
    data: Dict
    raw_frame: bytes


class MeshCoreDecoder:
    """Decodes MeshCore frames into structured events"""
    
    # Frame type definitions
    FRAME_TYPES = {
        0x00: "OK",
        0x01: "ERROR",
        0x02: "CONTACTS_START",
        0x03: "CONTACT",
        0x04: "END_CONTACTS",
        0x05: "SELF_INFO",
        0x06: "SENT",
        0x07: "CONTACT_MSG",
        0x08: "CHANNEL_MSG",
        0x09: "TIME",
        0x0A: "NO_MORE_MSG",
        0x0D: "DEVICE_INFO",
        0x10: "CHANNEL_MSG_DM",
        0x11: "CHANNEL_MSG_PUBLIC",
        0x80: "ADVERT",
        0x82: "ACK",
        0x83: "MSG_WAITING",
        0x84: "RAW_DATA",
        0x88: "MESH_PACKET",
        0x89: "TRACE"
    }
    
    def __init__(self, event_callback: Callable[[MeshEvent], None]):
        self.event_callback = event_callback
        self.contacts: Dict[str, str] = {}  # pubkey -> name mapping
        self.initial_contact_loading = True  # Flag for startup contact loading
        self.initial_contacts = []  # Track contacts during initial load
        self.stats = {
            'frames': 0,
            'messages': 0,
            'mesh_packets': 0,
            'adverts': 0
        }
        
    def decode_frame(self, frame: bytes):
        """Decode a frame and emit event"""
        if not frame or len(frame) < 1:
            return
            
        self.stats['frames'] += 1
        code = frame[0]
        frame_type = self.FRAME_TYPES.get(code, f"UNKNOWN_0x{code:02X}")
        
        logger.info(f"Decoding: 0x{code:02X} ({frame_type}) len={len(frame)}")
        logger.debug(f"Frame hex: {frame.hex()}")
        
        # Route to specific decoder
        event = None
        
        if code == 0x08:  # Channel message (old format)
            logger.info("*** CHANNEL MESSAGE DETECTED ***")
            event = self._decode_channel_message(frame)
        elif code == 0x10:  # Channel message (DM format)
            logger.info("*** DIRECT MESSAGE (0x10 FORMAT) DETECTED ***")
            event = self._decode_0x10_message(frame)
        elif code == 0x11:  # Channel message (public/room format)
            logger.info("*** PUBLIC CHANNEL MESSAGE (0x11 FORMAT) DETECTED ***")
            event = self._decode_0x11_message(frame)
        elif code == 0x07:  # Direct message
            logger.info("*** DIRECT MESSAGE DETECTED ***")
            event = self._decode_direct_message(frame)
        elif code == 0x88:  # Mesh packet
            event = self._decode_mesh_packet(frame)
        elif code == 0x80:  # Advertisement
            event = self._decode_advertisement(frame)
        elif code == 0x03:  # Contact
            event = self._decode_contact(frame)
        elif code == 0x04:  # END_CONTACTS - finalize initial loading
            logger.info("END_CONTACTS received - finalizing initial contact load")
            self.finalize_initial_contacts()
            return  # Don't emit event for END_CONTACTS
        elif code == 0x82:  # ACK
            event = self._decode_ack(frame)
        elif code == 0x84:  # Raw data
            event = self._decode_raw_data(frame)
        elif code == 0x89:  # Trace
            event = self._decode_trace(frame)
        elif code == 0x83:  # Message waiting - just log, don't emit event
            logger.debug("Message waiting signal received (0x83)")
            return  # Don't emit event
            
        # Emit event if decoded
        if event and self.event_callback:
            try:
                logger.info(f"Emitting event: {event.type.value}")
                self.event_callback(event)
            except Exception as e:
                logger.error(f"Error in event callback: {e}", exc_info=True)
                
    def _decode_0x11_message(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x11 public channel message"""
        if len(frame) < 11:
            logger.warning(f"0x11 message too short: {len(frame)} bytes")
            return None
            
        self.stats['messages'] += 1
        
        # Format: 0x11 + counter(3) + channel(1) + flags/hops(1) + timestamp(4) + hop_flag(1) + text
        counter = frame[1:4].hex()
        channel = frame[4]  # Channel number!
        hops = frame[5] if frame[5] != 0xFF else 0  # Hop count in flags byte
        
        if len(frame) >= 10:
            timestamp_bytes = frame[6:10]
            timestamp = struct.unpack('<I', timestamp_bytes)[0]
        else:
            timestamp = 0
        
        # Byte 10 is a flag (0x68 = 'h' but not part of text), text starts at byte 11
        hop_flag = frame[10] if len(frame) > 10 else 0
            
        # Text starts at byte 11 and contains "sender: message" format
        full_text = frame[11:].decode('utf-8', errors='ignore') if len(frame) > 11 else ""
        
        # Extract sender from "Name: message" format
        sender = f"Channel #{channel}"
        message = full_text
        if ': ' in full_text:
            parts = full_text.split(': ', 1)
            sender = parts[0]
            message = parts[1]
        
        logger.info(f"0x11 Channel: ch=#{channel}, hops={hops}, sender='{sender}', message='{message}'")
        
        return MeshEvent(
            type=EventType.CHANNEL_MESSAGE,
            timestamp=datetime.now(),
            data={
                'sender': sender,
                'message': message,
                'channel': channel,
                'hops': hops,
                'timestamp': timestamp
            },
            raw_frame=frame
        )
                
    def _decode_0x10_message(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x10 channel message (new format)"""
        if len(frame) < 16:
            logger.warning(f"0x10 message too short: {len(frame)} bytes")
            return None
            
        self.stats['messages'] += 1
        
        # Format: 0x10 + counter(3) + sender_key(6) + flags(2) + timestamp(4) + text
        sender_key = frame[4:10].hex()
        hops = frame[10] if frame[10] != 0xFF else 0  # First flag byte - hop count
        timestamp_bytes = frame[12:16] if len(frame) >= 16 else b'\x00\x00\x00\x00'
        timestamp = struct.unpack('<I', timestamp_bytes)[0]
        text = frame[16:].decode('utf-8', errors='ignore') if len(frame) > 16 else ""
        
        # Look up sender name
        sender = self.contacts.get(sender_key[:12], sender_key[:8] + '...')
        
        logger.info(f"0x10 DM: from={sender} ({sender_key[:12]}), hops={hops}, text='{text}'")
        
        return MeshEvent(
            type=EventType.DIRECT_MESSAGE,
            timestamp=datetime.now(),
            data={
                'sender': sender,
                'message': text,
                'channel': 0,
                'hops': hops,
                'timestamp': timestamp
            },
            raw_frame=frame
        )
                
    def _decode_channel_message(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x08 channel message"""
        if len(frame) < 8:
            logger.warning(f"Channel message too short: {len(frame)} bytes")
            return None
            
        self.stats['messages'] += 1
        
        channel = frame[1]
        hop_count = frame[2] if frame[2] != 0xFF else 0
        timestamp = struct.unpack('<I', frame[4:8])[0] if len(frame) >= 8 else 0
        text = frame[8:].decode('utf-8', errors='ignore') if len(frame) > 8 else ""
        
        logger.info(f"Channel message: ch={channel}, hops={hop_count}, text='{text}'")
        
        # Extract sender name from "Name: message" format
        sender = "Unknown"
        message = text
        if ': ' in text:
            parts = text.split(': ', 1)
            sender = parts[0]
            message = parts[1]
            
        return MeshEvent(
            type=EventType.CHANNEL_MESSAGE,
            timestamp=datetime.now(),
            data={
                'sender': sender,
                'message': message,
                'channel': channel,
                'hops': hop_count,
                'timestamp': timestamp
            },
            raw_frame=frame
        )
        
    def _decode_direct_message(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x07 direct message"""
        if len(frame) < 13:
            logger.warning(f"Direct message too short: {len(frame)} bytes")
            return None
            
        self.stats['messages'] += 1
        
        sender_key = frame[1:7].hex()
        hop_count = frame[7] if frame[7] != 0xFF else 0
        text = frame[13:].decode('utf-8', errors='ignore') if len(frame) > 13 else ""
        
        # Look up sender name
        sender = self.contacts.get(sender_key[:12], sender_key[:8] + '...')
        
        logger.info(f"Direct message: from={sender}, hops={hop_count}, text='{text}'")
        
        return MeshEvent(
            type=EventType.DIRECT_MESSAGE,
            timestamp=datetime.now(),
            data={
                'sender': sender,
                'sender_key': sender_key,
                'message': text,
                'hops': hop_count
            },
            raw_frame=frame
        )
        
    def _decode_mesh_packet(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x88 mesh packet"""
        self.stats['mesh_packets'] += 1
        data = frame[1:]  # Skip frame code
        
        packet_data = {
            'length': len(data),
            'hex': data.hex()
        }
        
        if len(data) < 2:
            return MeshEvent(
                type=EventType.MESH_PACKET,
                timestamp=datetime.now(),
                data=packet_data,
                raw_frame=frame
            )
            
        # Header bytes
        packet_data['header'] = f"{data[0]:02x}{data[1]:02x}"
        
        # Long packets are usually advertisements
        if len(data) > 100:
            packet_data['subtype'] = 'ADVERTISEMENT'
            
            # Look for public key (32 bytes)
            for offset in [4, 6, 8]:
                if len(data) >= offset + 32:
                    possible_key = data[offset:offset+32]
                    if any(b != 0 and b != 0xFF for b in possible_key):
                        packet_data['pubkey'] = possible_key.hex()
                        break
                        
            # Extract node name from end of packet
            if b'\x00' in data[-50:]:
                tail = data[-50:]
                parts = tail.split(b'\x00')
                for part in reversed(parts):
                    if len(part) > 3:
                        try:
                            name = part.decode('utf-8', errors='ignore')
                            if all(c.isprintable() or c in '-_' for c in name):
                                packet_data['node_name'] = name
                                break
                        except:
                            pass
                            
        elif len(data) < 20:
            packet_data['subtype'] = 'BEACON'
        else:
            packet_data['subtype'] = 'DATA'
            
        return MeshEvent(
            type=EventType.MESH_PACKET,
            timestamp=datetime.now(),
            data=packet_data,
            raw_frame=frame
        )
        
    def _decode_advertisement(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x80 advertisement"""
        if len(frame) < 33:
            return None
            
        self.stats['adverts'] += 1
        
        pubkey = frame[1:33].hex()
        name = self.contacts.get(pubkey[:12], 'Unknown')
        
        return MeshEvent(
            type=EventType.ADVERTISEMENT,
            timestamp=datetime.now(),
            data={
                'pubkey': pubkey,
                'name': name
            },
            raw_frame=frame
        )
        
    def _decode_contact(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x03 contact info"""
        if len(frame) < 100:
            return None
            
        pubkey = frame[1:33].hex()
        node_type = frame[33] if len(frame) > 33 else 0
        name_data = frame[100:132] if len(frame) >= 132 else b''
        name = name_data.decode('utf-8', errors='ignore').rstrip('\x00')
        
        types = {1: 'CHAT', 2: 'REPEATER', 3: 'ROOM'}
        type_str = types.get(node_type, f'TYPE_{node_type}')
        
        # Cache contact
        self.contacts[pubkey[:12]] = name
        
        # If we're in initial loading mode, cache silently
        if self.initial_contact_loading:
            self.initial_contacts.append({
                'name': name,
                'type': type_str,
                'pubkey': pubkey
            })
            logger.debug(f"Cached contact: {name} ({type_str})")
            return None  # Don't emit event during initial load
        
        # After initial load, emit events for new contacts
        logger.info(f"New contact discovered: {name} ({type_str})")
        
        return MeshEvent(
            type=EventType.CONTACT,
            timestamp=datetime.now(),
            data={
                'pubkey': pubkey,
                'name': name,
                'node_type': type_str
            },
            raw_frame=frame
        )
        
    def _decode_ack(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x82 ACK"""
        if len(frame) < 9:
            return None
            
        ack_code = frame[1:5].hex()
        rtt = struct.unpack('<I', frame[5:9])[0]
        
        return MeshEvent(
            type=EventType.ACK,
            timestamp=datetime.now(),
            data={
                'ack_code': ack_code,
                'rtt_ms': rtt
            },
            raw_frame=frame
        )
        
    def _decode_raw_data(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x84 raw data with signal info"""
        if len(frame) < 4:
            return None
            
        snr = struct.unpack('b', bytes([frame[1]]))[0] / 4.0
        rssi = struct.unpack('b', bytes([frame[2]]))[0]
        payload = frame[4:].hex() if len(frame) > 4 else ""
        
        return MeshEvent(
            type=EventType.RAW_DATA,
            timestamp=datetime.now(),
            data={
                'snr_db': snr,
                'rssi_dbm': rssi,
                'payload': payload
            },
            raw_frame=frame
        )
        
    def _decode_trace(self, frame: bytes) -> Optional[MeshEvent]:
        """Decode 0x89 trace packet with full path and SNR information
        
        Format (PUSH_CODE_TRACE_DATA):
        - Byte 0: 0x89 (code)
        - Byte 1: reserved (0)
        - Byte 2: path_len
        - Byte 3: flags
        - Bytes 4-7: tag (32-bit LE)
        - Bytes 8-11: auth_code (32-bit LE)
        - Bytes 12+: path_hashes (path_len bytes)
        - After path_hashes: path_snrs (path_len+1 bytes, each = SNR*4)
        """
        if len(frame) < 12:
            # Too short for minimal trace packet
            return MeshEvent(
                type=EventType.TRACE,
                timestamp=datetime.now(),
                data={'hex': frame.hex(), 'error': 'packet too short'},
                raw_frame=frame
            )
        
        try:
            reserved = frame[1]
            path_len = frame[2]
            flags = frame[3]
            tag = struct.unpack('<i', frame[4:8])[0]  # signed 32-bit
            auth_code = struct.unpack('<i', frame[8:12])[0]  # signed 32-bit
            
            # Calculate expected frame length
            expected_len = 12 + path_len + (path_len + 1)
            
            if len(frame) < expected_len:
                logger.warning(f"Trace packet shorter than expected: {len(frame)} < {expected_len}")
            
            # Extract path hashes
            path_hashes = []
            if len(frame) >= 12 + path_len:
                for i in range(path_len):
                    hash_byte = frame[12 + i]
                    path_hashes.append(f"{hash_byte:02x}")
            
            # Extract SNR values
            path_snrs = []
            snr_start = 12 + path_len
            if len(frame) >= snr_start + (path_len + 1):
                for i in range(path_len + 1):
                    snr_byte = struct.unpack('b', bytes([frame[snr_start + i]]))[0]  # signed byte
                    snr_db = snr_byte / 4.0
                    path_snrs.append(snr_db)
            
            data = {
                'hex': frame.hex(),
                'path_len': path_len,
                'flags': flags,
                'tag': tag,
                'auth_code': auth_code,
                'path_hashes': path_hashes,
                'path_snrs': path_snrs
            }
            
            logger.info(f"Trace packet decoded: path_len={path_len}, hops={len(path_snrs)-1}, avg_snr={sum(path_snrs)/len(path_snrs) if path_snrs else 0:.1f}dB")
            
        except Exception as e:
            logger.error(f"Error decoding trace packet: {e}")
            data = {
                'hex': frame.hex(),
                'error': str(e)
            }
        
        return MeshEvent(
            type=EventType.TRACE,
            timestamp=datetime.now(),
            data=data,
            raw_frame=frame
        )
    
    def finalize_initial_contacts(self):
        """End initial contact loading and emit summary event"""
        if not self.initial_contact_loading:
            logger.debug("finalize_initial_contacts called but already finalized")
            return  # Already finalized
        
        self.initial_contact_loading = False
        
        # Count by type
        type_counts = {}
        for contact in self.initial_contacts:
            node_type = contact['type']
            type_counts[node_type] = type_counts.get(node_type, 0) + 1
        
        total = len(self.initial_contacts)
        logger.info(f"Initial contact loading complete: {total} contacts cached")
        
        # Emit summary event
        if self.event_callback:
            event = MeshEvent(
                type=EventType.CONTACT_SUMMARY,
                timestamp=datetime.now(),
                data={
                    'total': total,
                    'by_type': type_counts,
                    'contacts': self.initial_contacts
                },
                raw_frame=b''
            )
            try:
                self.event_callback(event)
            except Exception as e:
                logger.error(f"Error emitting contact summary: {e}", exc_info=True)
        
        # Clear initial contacts list to free memory
        self.initial_contacts = []
