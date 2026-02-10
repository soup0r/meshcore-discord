"""
MeshCore TCP Connection Handler
Manages async TCP connection to MeshCore WiFi node
"""

import asyncio
import struct
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class MeshCoreConnection:
    """Handles TCP connection to MeshCore node with frame extraction"""
    
    def __init__(
        self,
        host: str,
        port: int,
        frame_callback: Callable,
        auto_reconnect: bool = True,
        reconnect_delay: int = 5
    ):
        self.host = host
        self.port = port
        self.frame_callback = frame_callback
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.buffer = bytearray()
        self.connected = False
        self.running = False
        
    async def connect(self) -> bool:
        """Establish TCP connection to MeshCore node"""
        try:
            logger.info(f"Connecting to MeshCore node at {self.host}:{self.port}...")
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            logger.info(f"✓ Connected to {self.host}:{self.port}")
            
            # Initialize the connection
            await self.initialize()
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.connected = False
            return False
            
    async def disconnect(self):
        """Close the connection"""
        self.running = False
        self.connected = False
        
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
                
        logger.info("Disconnected from MeshCore node")
        
    async def send_frame(self, cmd_code: int, data: bytes = b''):
        """Send command frame to MeshCore node"""
        if not self.writer or not self.connected:
            logger.warning("Cannot send frame - not connected")
            return
            
        try:
            frame_data = bytes([cmd_code]) + data
            frame = bytes([0x3C]) + struct.pack('<H', len(frame_data)) + frame_data
            self.writer.write(frame)
            await self.writer.drain()
            logger.debug(f"Sent frame: cmd=0x{cmd_code:02X}, len={len(data)}")
        except Exception as e:
            logger.error(f"Error sending frame: {e}")
            self.connected = False
            
    async def initialize(self):
        """Initialize MeshCore connection"""
        logger.info("Initializing MeshCore companion radio...")
        
        # Query device
        await self.send_frame(22, bytes([5]))  # CMD_DEVICE_QUERY, protocol v5
        await asyncio.sleep(0.3)
        
        # Start app
        app_name = b"Discord Bridge"
        await self.send_frame(1, bytes([1,0,0,0,0,0,0]) + app_name)  # CMD_APP_START
        await asyncio.sleep(0.3)
        
        # Get contacts for name resolution
        logger.info("Loading contacts...")
        await self.send_frame(4)  # CMD_GET_CONTACTS
        # Note: finalize_initial_contacts() is called when END_CONTACTS (0x04) is received
        await asyncio.sleep(1.0)  # Give time for contacts to load
        
        # Sync messages - try multiple times
        logger.info("Requesting messages from network...")
        for i in range(5):
            await self.send_frame(10)  # CMD_SYNC_NEXT_MESSAGE
            await asyncio.sleep(0.3)
        
        logger.info("MeshCore initialization complete")
        
    def process_buffer(self):
        """Extract frames from buffer"""
        while len(self.buffer) >= 3:
            if self.buffer[0] == 0x3E:  # '>' frame marker from node
                frame_len = struct.unpack('<H', self.buffer[1:3])[0]
                
                if len(self.buffer) >= 3 + frame_len:
                    # Extract complete frame
                    frame = bytes(self.buffer[3:3 + frame_len])
                    self.buffer = self.buffer[3 + frame_len:]
                    
                    # DEBUG: Log incoming frame
                    if len(frame) > 0:
                        logger.info(f"<<< Frame RX: 0x{frame[0]:02X} len={len(frame)}")
                    
                    # Handle MSG_WAITING - immediately request sync
                    if len(frame) > 0 and frame[0] == 0x83:
                        logger.info("Message waiting detected - requesting sync")
                        asyncio.create_task(self.send_frame(10))
                    
                    # Pass to callback
                    if self.frame_callback:
                        try:
                            self.frame_callback(frame)
                        except Exception as e:
                            logger.error(f"Error in frame callback: {e}", exc_info=True)
                else:
                    # Wait for more data
                    break
            else:
                # Invalid byte, discard
                self.buffer.pop(0)
                
    async def read_loop(self):
        """Main read loop - continuously read from TCP socket"""
        self.running = True
        
        while self.running:
            try:
                if not self.connected:
                    if self.auto_reconnect:
                        logger.info(f"Attempting reconnection in {self.reconnect_delay}s...")
                        await asyncio.sleep(self.reconnect_delay)
                        await self.connect()
                    else:
                        break
                        
                # Read data from socket
                data = await asyncio.wait_for(
                    self.reader.read(4096),
                    timeout=1.0
                )
                
                if not data:
                    # Connection closed
                    logger.warning("Connection closed by remote host")
                    self.connected = False
                    continue
                    
                # Add to buffer and process
                self.buffer.extend(data)
                self.process_buffer()
                
            except asyncio.TimeoutError:
                # Normal timeout, just continue
                continue
                
            except Exception as e:
                logger.error(f"Error in read loop: {e}", exc_info=True)
                self.connected = False
                await asyncio.sleep(1)
                
        logger.info("Read loop terminated")
        
    async def periodic_sync(self):
        """Periodically sync messages from the network"""
        while self.running:
            try:
                await asyncio.sleep(30)
                if self.connected:
                    logger.info("Periodic sync - requesting messages")
                    await self.send_frame(10)  # CMD_SYNC_NEXT_MESSAGE
            except Exception as e:
                logger.error(f"Error in periodic sync: {e}")
