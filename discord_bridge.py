"""
Discord Bridge
Routes MeshCore events to Discord channels
"""

import asyncio
import discord
import logging
from typing import Dict, List
from datetime import datetime
from collections import deque

from meshcore_decoder import MeshEvent, EventType

logger = logging.getLogger(__name__)


class DiscordBridge(discord.Client):
    """Bridges MeshCore events to Discord channels"""
    
    # Channel name mapping - customize this for your network
    CHANNEL_NAMES = {
        0: "Public",
        1: "NorNIron",
        # Add more channels as needed:
        # 2: "Private",
        # 3: "Test",
    }
    
    def __init__(
        self,
        token: str,
        channel_ids: Dict[str, int],
        batch_interval: float = 2.0,
        max_batch_size: int = 10,
        **kwargs
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **kwargs)
        
        self.token = token
        self.channel_ids = channel_ids
        self.batch_interval = batch_interval
        self.max_batch_size = max_batch_size
        
        # Message queues for batching
        self.message_queue: deque = deque()
        self.info_queue: deque = deque()
        
        # Channel cache
        self.channels: Dict[str, discord.TextChannel] = {}
        
        # Stats
        self.stats = {
            'events_received': 0,
            'messages_sent': 0,
            'errors': 0
        }
        
    async def on_ready(self):
        """Called when Discord bot is ready"""
        logger.info(f"Discord bot logged in as {self.user}")
        
        # Cache channels
        for name, channel_id in self.channel_ids.items():
            try:
                channel = await self.fetch_channel(channel_id)
                self.channels[name] = channel
                logger.info(f"✓ Connected to #{channel.name} ({name})")
            except Exception as e:
                logger.error(f"Failed to fetch channel {name} ({channel_id}): {e}")
                
        # Start batch sender
        self.loop.create_task(self.batch_sender())
        
    async def start_bot(self):
        """Start the Discord bot"""
        try:
            await self.start(self.token)
        except Exception as e:
            logger.error(f"Failed to start Discord bot: {e}")
            raise
            
    def handle_event(self, event: MeshEvent):
        """Handle incoming MeshCore event"""
        self.stats['events_received'] += 1
        
        try:
            # Route to appropriate channel
            if event.type in [EventType.CHANNEL_MESSAGE, EventType.DIRECT_MESSAGE]:
                if event.type == EventType.DIRECT_MESSAGE:
                    logger.info(f"!!! ROUTING DM TO #meshcore-messages !!! From: {event.data.get('sender')}")
                else:
                    logger.info(f"!!! ROUTING CHANNEL MESSAGE TO #meshcore-messages !!! Ch#{event.data.get('channel')} From: {event.data.get('sender')}")
                logger.debug(f"Message data: {event.data}")
                embed = self._create_message_embed(event)
                self.message_queue.append(embed)
                logger.debug(f"Message queue size: {len(self.message_queue)}")
                
            else:
                # Everything else goes to info channel
                embed = self._create_info_embed(event)
                self.info_queue.append(embed)
                
        except Exception as e:
            logger.error(f"Error handling event: {e}", exc_info=True)
            self.stats['errors'] += 1
            
    def _create_message_embed(self, event: MeshEvent) -> discord.Embed:
        """Create embed for message events"""
        data = event.data
        
        if event.type == EventType.CHANNEL_MESSAGE:
            # Public channel message (0x11)
            channel_num = data['channel']
            channel_name = self.CHANNEL_NAMES.get(channel_num, f"#{channel_num}")
            
            embed = discord.Embed(
                title="💬 Channel Message",
                description=data['message'],
                color=discord.Color.green(),
                timestamp=event.timestamp
            )
            embed.add_field(name="From", value=data['sender'], inline=True)
            embed.add_field(name="Channel", value=channel_name, inline=True)
            embed.add_field(name="Hops", value=str(data['hops']), inline=True)
            
        else:  # Direct message (0x10 or 0x07)
            embed = discord.Embed(
                title="📨 Direct Message",
                description=data['message'],
                color=discord.Color.blue(),
                timestamp=event.timestamp
            )
            embed.add_field(name="From", value=data['sender'], inline=True)
            embed.add_field(name="Hops", value=str(data['hops']), inline=True)
            # No channel field for DMs
            
        return embed
        
    def _create_info_embed(self, event: MeshEvent) -> discord.Embed:
        """Create embed for info events"""
        data = event.data
        
        if event.type == EventType.MESH_PACKET:
            # Only show interesting mesh packets (with node names)
            if 'node_name' not in data:
                return None  # Skip boring packets
                
            embed = discord.Embed(
                title="📡 Mesh Node Detected",
                color=discord.Color.purple(),
                timestamp=event.timestamp
            )
            embed.add_field(name="Node", value=data.get('node_name', 'Unknown'), inline=True)
            embed.add_field(name="Type", value=data.get('subtype', 'Unknown'), inline=True)
            
            if 'pubkey' in data:
                embed.add_field(
                    name="Key",
                    value=f"`{data['pubkey'][:16]}...`",
                    inline=False
                )
                
        elif event.type == EventType.ADVERTISEMENT:
            embed = discord.Embed(
                title="📢 Node Advertisement",
                color=discord.Color.blue(),
                timestamp=event.timestamp
            )
            embed.add_field(name="Node", value=data['name'], inline=True)
            embed.add_field(name="Key", value=f"`{data['pubkey'][:16]}...`", inline=False)
            
        elif event.type == EventType.CONTACT:
            embed = discord.Embed(
                title="👤 New Contact",
                color=discord.Color.gold(),
                timestamp=event.timestamp
            )
            embed.add_field(name="Name", value=data['name'], inline=True)
            embed.add_field(name="Type", value=data['node_type'], inline=True)
            embed.add_field(name="Key", value=f"`{data['pubkey'][:16]}...`", inline=False)
            
        elif event.type == EventType.ACK:
            embed = discord.Embed(
                title="✅ ACK Received",
                color=discord.Color.green(),
                timestamp=event.timestamp
            )
            embed.add_field(name="Code", value=f"`{data['ack_code']}`", inline=True)
            embed.add_field(name="RTT", value=f"{data['rtt_ms']}ms", inline=True)
            
        elif event.type == EventType.RAW_DATA:
            embed = discord.Embed(
                title="📊 Signal Data",
                color=discord.Color.teal(),
                timestamp=event.timestamp
            )
            embed.add_field(name="SNR", value=f"{data['snr_db']:.1f} dB", inline=True)
            embed.add_field(name="RSSI", value=f"{data['rssi_dbm']} dBm", inline=True)
            
        elif event.type == EventType.TRACE:
            embed = discord.Embed(
                title="🔄 Trace Packet",
                description=f"`{data['hex'][:50]}...`",
                color=discord.Color.dark_gray(),
                timestamp=event.timestamp
            )
            
        else:
            # Generic fallback
            embed = discord.Embed(
                title=f"ℹ️ {event.type.value.replace('_', ' ').title()}",
                color=discord.Color.light_gray(),
                timestamp=event.timestamp
            )
            
        return embed
        
    async def batch_sender(self):
        """Send batched messages to Discord"""
        await self.wait_until_ready()
        logger.info("Batch sender started")
        
        while not self.is_closed():
            try:
                await asyncio.sleep(self.batch_interval)
                
                # Send message queue
                if self.message_queue and 'messages' in self.channels:
                    logger.info(f"Sending {len(self.message_queue)} messages to #meshcore-messages")
                    await self._send_batch(
                        self.message_queue,
                        self.channels['messages']
                    )
                    
                # Send info queue
                if self.info_queue and 'info' in self.channels:
                    await self._send_batch(
                        self.info_queue,
                        self.channels['info']
                    )
                    
            except Exception as e:
                logger.error(f"Error in batch sender: {e}", exc_info=True)
                self.stats['errors'] += 1
                
    async def _send_batch(self, queue: deque, channel: discord.TextChannel):
        """Send a batch of embeds to a channel"""
        batch = []
        
        # Collect up to max_batch_size items
        while queue and len(batch) < self.max_batch_size:
            embed = queue.popleft()
            if embed:  # Skip None embeds
                batch.append(embed)
                
        if not batch:
            return
            
        try:
            # Send all embeds without pinging anyone
            for embed in batch:
                await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                    silent=True
                )
                self.stats['messages_sent'] += 1
                
            logger.debug(f"Sent {len(batch)} embeds to #{channel.name}")
            
        except discord.errors.HTTPException as e:
            logger.error(f"Discord API error: {e}")
            self.stats['errors'] += 1
            
            # Re-queue on rate limit
            if e.status == 429:
                logger.warning("Rate limited! Re-queuing messages")
                for embed in batch:
                    queue.appendleft(embed)
                await asyncio.sleep(5)
                
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}", exc_info=True)
            self.stats['errors'] += 1
            
    def get_stats(self) -> Dict:
        """Get bridge statistics"""
        return {
            **self.stats,
            'message_queue_size': len(self.message_queue),
            'info_queue_size': len(self.info_queue)
        }
