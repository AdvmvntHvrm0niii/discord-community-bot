"""Server statistics: the /stats embed and per-member message tracking."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("portfolio_bot.stats")

RANK_ICONS = {1: "🥇", 2: "🥈", 3: "🥉"}


class Stats(commands.Cog):
    """Counts messages per member and reports server statistics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Record one message of activity for the author."""
        if message.guild is None or message.author.bot:
            return
        try:
            self.bot.db.record_message(message.guild.id, message.author.id)
        except Exception:
            # Activity tracking must never break message handling.
            logger.exception("Could not record message activity")

    @app_commands.command(name="stats", description="Show server statistics.")
    @app_commands.guild_only()
    async def stats(self, interaction: discord.Interaction) -> None:
        guild, db = interaction.guild, self.bot.db

        total_tickets, open_tickets = db.get_ticket_counts(guild.id)
        top_members = [
            row for row in db.get_top_members(guild.id, 10) if row["message_count"] > 0
        ]

        embed = discord.Embed(
            title=f"📊 {guild.name} — Statistics",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="👥 Members", value=f"{guild.member_count or 0:,}")
        embed.add_field(
            name="🎫 Tickets",
            value=f"{total_tickets:,} total\n{open_tickets:,} open",
        )
        embed.add_field(name="⚠️ Warnings", value=f"{db.count_warnings(guild.id):,}")
        embed.add_field(
            name="🎉 Active giveaways",
            value=f"{db.count_active_giveaways(guild.id):,}",
        )

        if top_members:
            lines = [
                f"{RANK_ICONS.get(rank, f'**{rank}.**')} <@{row['user_id']}> — "
                f"{row['message_count']:,} messages"
                for rank, row in enumerate(top_members, start=1)
            ]
            top_text = "\n".join(lines)
        else:
            top_text = "No activity recorded yet — start chatting!"
        embed.add_field(name="🏆 Top 10 active members", value=top_text, inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Entry point used by ``bot.load_extension``."""
    await bot.add_cog(Stats(bot))
