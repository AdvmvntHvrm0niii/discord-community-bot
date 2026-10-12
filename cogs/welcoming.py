"""Welcome messages and automatic role assignment for new members.

Settings live in the shared ``guild_settings`` table and are managed by admins
with ``/welcome set``. A missing or deleted channel/role is skipped safely.
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("portfolio_bot.welcoming")

CHANNEL_KEY = "welcome_channel_id"
ROLE_KEY = "welcome_role_id"
MESSAGE_KEY = "welcome_message"
DEFAULT_MESSAGE = "Welcome to **{server}**, {user}! 🎉 We're glad you're here."


class Welcoming(commands.Cog):
    """Greets new members and gives them the configured role."""

    welcome = app_commands.Group(
        name="welcome",
        description="Configure the welcome message and auto-role (admin only).",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _get_id(self, guild_id: int, key: str) -> int | None:
        value = self.bot.db.get_setting(guild_id, key)
        return int(value) if value and value.isdigit() else None

    # ------------------------------------------------------------------ #
    #  Listener
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        await self._assign_role(member)
        await self._send_welcome(member)

    async def _assign_role(self, member: discord.Member) -> None:
        role_id = self._get_id(member.guild.id, ROLE_KEY)
        if role_id is None:
            return
        role = member.guild.get_role(role_id)
        if role is None:
            logger.warning("Auto-role %s no longer exists in guild %s", role_id, member.guild.id)
            return
        try:
            await member.add_roles(role, reason="Automatic role on join")
        except discord.Forbidden:
            logger.warning("Cannot assign %s in guild %s (check role order)", role.name, member.guild.id)
        except discord.HTTPException as exc:
            logger.warning("Auto-role failed in guild %s: %s", member.guild.id, exc)

    async def _send_welcome(self, member: discord.Member) -> None:
        guild = member.guild
        channel_id = self._get_id(guild.id, CHANNEL_KEY)
        if channel_id is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Welcome channel %s missing in guild %s", channel_id, guild.id)
            return
        if not channel.permissions_for(guild.me).send_messages:
            logger.warning("Cannot send in welcome channel #%s", channel.name)
            return

        template = self.bot.db.get_setting(guild.id, MESSAGE_KEY) or DEFAULT_MESSAGE
        text = template.replace("{user}", member.mention).replace("{server}", guild.name)
        embed = discord.Embed(description=text, color=discord.Color.green())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{guild.member_count}")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.warning("Welcome message failed in guild %s: %s", guild.id, exc)

    # ------------------------------------------------------------------ #
    #  /welcome set | show  (admin only)
    # ------------------------------------------------------------------ #

    @welcome.command(name="set", description="Set the welcome channel, auto-role or message.")
    @app_commands.describe(
        channel="Channel for welcome messages",
        role="Role given to every new member",
        message="Text to send. Use {user} and {server} as placeholders",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_set(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        role: Optional[discord.Role] = None,
        message: Optional[app_commands.Range[str, 1, 500]] = None,
    ) -> None:
        if channel is None and role is None and message is None:
            await interaction.response.send_message(
                "Provide at least one of `channel`, `role` or `message`.", ephemeral=True
            )
            return

        guild, db, notes = interaction.guild, self.bot.db, []
        if channel is not None:
            if not channel.permissions_for(guild.me).send_messages:
                await interaction.response.send_message(
                    f"❌ I can't send messages in {channel.mention}.", ephemeral=True
                )
                return
            db.set_setting(guild.id, CHANNEL_KEY, str(channel.id))
            notes.append(f"Channel: {channel.mention}")
        if role is not None:
            if role.is_default() or role.managed:
                await interaction.response.send_message(
                    "❌ That role can't be assigned automatically.", ephemeral=True
                )
                return
            if role >= guild.me.top_role:
                await interaction.response.send_message(
                    f"❌ {role.mention} is above my highest role. Move my role higher "
                    "in Server Settings → Roles.",
                    ephemeral=True,
                )
                return
            db.set_setting(guild.id, ROLE_KEY, str(role.id))
            notes.append(f"Role: {role.mention}")
        if message is not None:
            db.set_setting(guild.id, MESSAGE_KEY, message)
            notes.append("Message updated")

        await interaction.response.send_message(
            "✅ Saved.\n" + "\n".join(notes),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @welcome.command(name="show", description="Show the current welcome settings.")
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_show(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        channel_id = self._get_id(guild.id, CHANNEL_KEY)
        role_id = self._get_id(guild.id, ROLE_KEY)
        message = self.bot.db.get_setting(guild.id, MESSAGE_KEY) or DEFAULT_MESSAGE

        embed = discord.Embed(title="Welcome settings", color=discord.Color.green())
        embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "Not set")
        embed.add_field(name="Auto-role", value=f"<@&{role_id}>" if role_id else "Not set")
        embed.add_field(name="Message", value=message, inline=False)
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot) -> None:
    """Entry point used by ``bot.load_extension``."""
    await bot.add_cog(Welcoming(bot))
