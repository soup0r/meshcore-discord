#!/usr/bin/env python3
"""
MeshCore to Discord Bridge
Connects to MeshCore WiFi node and forwards traffic to Discord
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml

from meshcore_connection import MeshCoreConnection
from meshcore_decoder import MeshCoreDecoder
from discord_bridge import DiscordBridge


class MeshCoreDiscordBridge:
    """Main application - coordinates connection, decoder, and Discord"""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.running = False
        
        # Components
        self.connection = None
        self.decoder = None
        self.discord = None
        
        # Setup logging
        self._setup_logging()
        
    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file"""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                
            # Validate required fields
            required = ['meshcore', 'discord']
            for field in required:
                if field not in config:
                    raise ValueError(f"Missing required config section: {field}")
                    
            if 'host' not in config['meshcore']:
                raise ValueError("Missing meshcore.host in config")
            if 'token' not in config['discord']:
                raise ValueError("Missing discord.token in config")
            if 'channels' not in config['discord']:
                raise ValueError("Missing discord.channels in config")
            
            # Validate at least one channel is configured
            channels = config['discord']['channels']
            if not channels:
                raise ValueError("No Discord channels configured")
                
            # Recommend having dm and info channels
            if 'dm' not in channels:
                print("Warning: No 'dm' channel configured - DMs won't be displayed")
            if 'info' not in channels:
                print("Warning: No 'info' channel configured - mesh info won't be displayed")
                
            return config
            
        except FileNotFoundError:
            print(f"Error: Config file not found: {config_path}")
            print("Please create a config.yaml file (see config.example.yaml)")
            sys.exit(1)
        except Exception as e:
            print(f"Error loading config: {e}")
            sys.exit(1)
            
    def _setup_logging(self):
        """Setup logging configuration"""
        log_config = self.config.get('logging', {})
        level = log_config.get('level', 'INFO')
        log_file = log_config.get('file', 'meshcore_discord.log')
        
        # Configure root logger
        logging.basicConfig(
            level=getattr(logging, level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        
        # Quiet down discord.py
        logging.getLogger('discord').setLevel(logging.WARNING)
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("Logging initialized")
        
    async def start(self):
        """Start the bridge"""
        self.running = True
        self.logger.info("=" * 60)
        self.logger.info("MeshCore to Discord Bridge")
        self.logger.info("=" * 60)
        
        try:
            # Initialize Discord bot
            self.logger.info("Starting Discord bot...")
            discord_config = self.config['discord']
            self.discord = DiscordBridge(
                token=discord_config['token'],
                channel_ids=discord_config['channels'],
                batch_interval=discord_config.get('batch_interval', 2.0),
                max_batch_size=discord_config.get('max_batch_size', 10)
            )
            
            # Start Discord bot in background
            discord_task = asyncio.create_task(self.discord.start_bot())
            
            # Wait for Discord to be ready (with timeout)
            self.logger.info("Waiting for Discord bot to be ready...")
            for i in range(20):  # Wait up to 10 seconds
                await asyncio.sleep(0.5)
                if self.discord.is_ready():
                    break
            else:
                self.logger.error("Discord bot failed to connect (timeout)")
                return
            
            # Log channel routing
            self.logger.info("Channel routing configured:")
            channels = discord_config['channels']
            if 'dm' in channels:
                self.logger.info("  - Direct Messages → #dm")
            for key in sorted(channels.keys()):
                if key.startswith('channel_'):
                    channel_num = key.split('_')[1]
                    self.logger.info(f"  - MeshCore Channel {channel_num} → #{key}")
            if 'info' in channels:
                self.logger.info("  - Mesh Info → #info")
                
            # Initialize decoder
            self.logger.info("Initializing decoder...")
            self.decoder = MeshCoreDecoder(
                event_callback=self.discord.handle_event
            )
            
            # Initialize MeshCore connection
            self.logger.info("Connecting to MeshCore node...")
            meshcore_config = self.config['meshcore']
            self.connection = MeshCoreConnection(
                host=meshcore_config['host'],
                port=meshcore_config.get('port', 4000),
                frame_callback=self.decoder.decode_frame,
                auto_reconnect=meshcore_config.get('auto_reconnect', True),
                reconnect_delay=meshcore_config.get('reconnect_delay', 5)
            )
            
            if not await self.connection.connect():
                self.logger.error("Failed to connect to MeshCore node")
                return
                
            # Start tasks
            self.logger.info("✓ Bridge is running!")
            self.logger.info(f"MeshCore: {meshcore_config['host']}:{meshcore_config.get('port', 4000)}")
            self.logger.info("Press Ctrl+C to stop")
            
            tasks = [
                asyncio.create_task(self.connection.read_loop()),
                asyncio.create_task(self.connection.periodic_sync()),
                asyncio.create_task(self.stats_reporter()),
                discord_task
            ]
            
            # Wait for tasks or until stopped
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                self.logger.info("Tasks cancelled")
                # Cancel all tasks
                for task in tasks:
                    if not task.done():
                        task.cancel()
                # Wait for cancellation to complete
                await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            self.logger.error(f"Error in bridge: {e}", exc_info=True)
        finally:
            await self.stop()
            
    async def stop(self):
        """Stop the bridge gracefully"""
        if not self.running:
            return
            
        self.running = False
        self.logger.info("Shutting down bridge...")
        
        # Disconnect MeshCore
        if self.connection:
            await self.connection.disconnect()
            
        # Close Discord with timeout
        if self.discord and not self.discord.is_closed():
            try:
                await asyncio.wait_for(self.discord.close(), timeout=3.0)
            except asyncio.TimeoutError:
                self.logger.warning("Discord close timed out")
            
        self.logger.info("Bridge stopped")
        
    async def stats_reporter(self):
        """Periodically report statistics"""
        while self.running:
            await asyncio.sleep(300)  # Every 5 minutes
            
            try:
                if self.decoder and self.discord:
                    decoder_stats = self.decoder.stats
                    discord_stats = self.discord.get_stats()
                    
                    self.logger.info("=" * 60)
                    self.logger.info("Statistics:")
                    self.logger.info(f"  Frames decoded: {decoder_stats['frames']}")
                    self.logger.info(f"  Messages: {decoder_stats['messages']}")
                    self.logger.info(f"  Mesh packets: {decoder_stats['mesh_packets']}")
                    self.logger.info(f"  Adverts: {decoder_stats['adverts']}")
                    self.logger.info(f"  Contacts: {len(self.decoder.contacts)}")
                    self.logger.info(f"  Discord events: {discord_stats['events_received']}")
                    self.logger.info(f"  Discord messages sent: {discord_stats['messages_sent']}")
                    
                    # Per-channel stats
                    if 'by_channel' in discord_stats:
                        self.logger.info("  By channel:")
                        for channel, count in sorted(discord_stats['by_channel'].items()):
                            self.logger.info(f"    - {channel}: {count}")
                    
                    self.logger.info(f"  Errors: {discord_stats['errors']}")
                    self.logger.info("=" * 60)
                    
            except Exception as e:
                self.logger.error(f"Error in stats reporter: {e}")


async def main():
    """Main entry point"""
    bridge = MeshCoreDiscordBridge()
    
    # Flag for shutdown
    shutdown_event = asyncio.Event()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutting down...")
        shutdown_event.set()
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create bridge task
    bridge_task = asyncio.create_task(bridge.start())
    
    # Wait for either bridge to finish or shutdown signal
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    done, pending = await asyncio.wait(
        [bridge_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED
    )
    
    # If shutdown was triggered, stop the bridge
    if shutdown_task in done:
        await bridge.stop()
        # Cancel bridge task if still running
        if not bridge_task.done():
            bridge_task.cancel()
            try:
                await bridge_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBridge stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
