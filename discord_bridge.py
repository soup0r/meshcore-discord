"""
Discord Bridge
Routes MeshCore events to Discord channels
"""

import asyncio
import discord
import logging
from typing import Dict, List
from datetime import datetime
from collections import deque, defaultdict

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
        
        # Message queues for batching - one per channel type
        self.dm_queue: deque = deque()
        self.channel_queues: Dict[int, deque] = defaultdict(deque)  # MeshCore channel# -> queue
        self.info_queue: deque = deque()
        
        # Channel cache
        self.channels: Dict[str, discord.TextChannel] = {}
        
        # Stats
        self.stats = {
            'events_received': 0,
            'messages_sent': 0,
            'errors': 0,
            'by_channel': defaultdict(int)
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
            if event.type == EventType.DIRECT_MESSAGE:
                # DMs go to dm channel
                logger.info(f"!!! ROUTING DM TO #dm !!! From: {event.data.get('sender')}")
                logger.debug(f"Message data: {event.data}")
                embed = self._create_message_embed(event)
                self.dm_queue.append(embed)
                self.stats['by_channel']['dm'] += 1
                logger.debug(f"DM queue size: {len(self.dm_queue)}")
                
            elif event.type == EventType.CHANNEL_MESSAGE:
                # Channel messages go to their specific channel
                channel_num = event.data.get('channel', 0)
                channel_key = f"channel_{channel_num}"
                logger.info(f"!!! ROUTING CHANNEL MESSAGE TO #{channel_key} !!! Ch#{channel_num} From: {event.data.get('sender')}")
                logger.debug(f"Message data: {event.data}")
                embed = self._create_message_embed(event)
                self.channel_queues[channel_num].append(embed)
                self.stats['by_channel'][channel_key] += 1
                logger.debug(f"Channel {channel_num} queue size: {len(self.channel_queues[channel_num])}")
            
            elif event.type == EventType.CONTACT_SUMMARY:
                # Contact summary goes to info channel
                logger.info("!!! ROUTING CONTACT SUMMARY TO #info !!!")
                embed = self._create_contact_summary_embed(event)
                self.info_queue.append(embed)
                self.stats['by_channel']['info'] += 1
                
            else:
                # Everything else goes to info channel
                embed = self._create_info_embed(event)
                self.info_queue.append(embed)
                self.stats['by_channel']['info'] += 1
                
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
    
    def _create_contact_summary_embed(self, event: MeshEvent) -> discord.Embed:
        """Create embed for contact summary"""
        data = event.data
        total = data.get('total', 0)
        by_type = data.get('by_type', {})
        
        embed = discord.Embed(
            title="🔄 Bridge Connected & Initialized",
            description=f"Successfully connected to MeshCore node and loaded **{total} contacts**",
            color=discord.Color.green(),
            timestamp=event.timestamp
        )
        
        # Add breakdown by type
        if by_type:
            type_breakdown = []
            for node_type, count in sorted(by_type.items()):
                emoji = {
                    'CHAT': '💬',
                    'REPEATER': '📡',
                    'ROOM': '🏠'
                }.get(node_type, '❓')
                type_breakdown.append(f"{emoji} {node_type}: {count}")
            
            embed.add_field(
                name="Contact Breakdown",
                value='\n'.join(type_breakdown),
                inline=False
            )
        
        embed.set_footer(text="All contacts cached for name resolution • Monitoring for new contacts")
        
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
                # Show full public key
                embed.add_field(
                    name="Key",
                    value=f"`{data['pubkey']}`",
                    inline=False
                )
                
        elif event.type == EventType.ADVERTISEMENT:
            embed = discord.Embed(
                title="📢 Node Advertisement",
                color=discord.Color.blue(),
                timestamp=event.timestamp
            )
            embed.add_field(name="Node", value=data['name'], inline=True)
            embed.add_field(name="Key", value=f"`{data['pubkey']}`", inline=False)
            
        elif event.type == EventType.CONTACT:
            embed = discord.Embed(
                title="👤 New Contact",
                color=discord.Color.gold(),
                timestamp=event.timestamp
            )
            embed.add_field(name="Name", value=data['name'], inline=True)
            embed.add_field(name="Type", value=data['node_type'], inline=True)
            embed.add_field(name="Key", value=f"`{data['pubkey']}`", inline=False)
            
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
            # Show decoded trace information if available
            if 'error' in data:
                embed = discord.Embed(
                    title="🔍 Trace Packet (Error)",
                    description=f"Error: {data['error']}",
                    color=discord.Color.red(),
                    timestamp=event.timestamp
                )
                embed.add_field(name="Raw Hex", value=f"`{data['hex'][:100]}...`", inline=False)
            elif 'path_len' in data:
                # Decoded trace packet
                path_len = data['path_len']
                hops = len(data.get('path_snrs', [])) - 1  # -1 because last SNR is to destination
                
                embed = discord.Embed(
                    title="🔍 Trace Packet",
                    description=f"Network path trace completed with **{hops} hops**",
                    color=discord.Color.teal(),
                    timestamp=event.timestamp
                )
                
                # Tag and auth code
                embed.add_field(name="Tag", value=f"`{data['tag']}`", inline=True)
                embed.add_field(name="Auth Code", value=f"`{data['auth_code']}`", inline=True)
                embed.add_field(name="Path Length", value=str(path_len), inline=True)
                
                # Path hashes
                if data.get('path_hashes'):
                    path_str = " → ".join(data['path_hashes'])
                    if len(path_str) > 1000:
                        path_str = path_str[:1000] + "..."
                    embed.add_field(
                        name="Path Hashes",
                        value=f"`{path_str}`",
                        inline=False
                    )
                
                # SNR values with visual indicators
                if data.get('path_snrs'):
                    snr_strs = []
                    for i, snr in enumerate(data['path_snrs']):
                        # Add visual indicator based on SNR quality
                        if snr >= 10:
                            indicator = "🟢"  # Excellent
                        elif snr >= 5:
                            indicator = "🟡"  # Good
                        elif snr >= 0:
                            indicator = "🟠"  # Fair
                        else:
                            indicator = "🔴"  # Poor
                        
                        hop_label = f"Hop {i}" if i < len(data['path_snrs']) - 1 else "Final"
                        snr_strs.append(f"{indicator} {hop_label}: {snr:.1f} dB")
                    
                    embed.add_field(
                        name="Signal Quality (SNR)",
                        value="\n".join(snr_strs),
                        inline=False
                    )
                    
                    # Calculate average SNR
                    avg_snr = sum(data['path_snrs']) / len(data['path_snrs'])
                    embed.set_footer(text=f"Average SNR: {avg_snr:.1f} dB")
                
                # Show hex for reference
                if len(data['hex']) > 100:
                    embed.add_field(
                        name="Raw Hex",
                        value=f"`{data['hex'][:100]}...` ({len(data['hex'])//2} bytes)",
                        inline=False
                    )
            else:
                # Fallback for unparsed trace
                embed = discord.Embed(
                    title="🔍 Trace Packet",
                    description=f"`{data.get('hex', '')}...`" if len(data.get('hex', '')) > 50 else f"`{data.get('hex', '')}`",
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
                
                # Send DM queue
                if self.dm_queue and 'dm' in self.channels:
                    logger.info(f"Sending {len(self.dm_queue)} messages to #dm")
                    await self._send_batch(
                        self.dm_queue,
                        self.channels['dm']
                    )
                
                # Send each channel queue
                for channel_num, queue in self.channel_queues.items():
                    channel_key = f"channel_{channel_num}"
                    if queue and channel_key in self.channels:
                        logger.info(f"Sending {len(queue)} messages to #{channel_key}")
                        await self._send_batch(
                            queue,
                            self.channels[channel_key]
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
            'dm_queue_size': len(self.dm_queue),
            'channel_queue_sizes': {f'channel_{k}': len(v) for k, v in self.channel_queues.items()},
            'info_queue_size': len(self.info_queue)
        }
