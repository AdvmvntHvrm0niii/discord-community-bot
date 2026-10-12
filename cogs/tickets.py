"""Ticket system: /setup (admin) configures it, /ticket opens a private channel.

Each ticket is a private text channel visible only to the author, the support
role and the bot. A persistent "Close Ticket" button inside the channel closes
the ticket, records it in SQLite and deletes the channel.
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("portfolio_bot.tickets")

CATEGORY_KEY = "ticket_category_id"
SUPPORT_ROLE_KEY = "ticket_support_role_id"
DELETE_DELAY_SECONDS = 5


class CloseTicketView(discord.ui.View):
    """Persistent view holding the Close button (survives bot restarts)."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)  # timeout=None + custom_id => persistent
        self.bot = bot

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket:close",
    )
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        db = self.bot.db
        ticket = db.get_ticket_by_channel(interaction.channel_id)
        if ticket is None or ticket["status"] != "open":
            await interaction.response.send_message(
                "This ticket is already closed.", ephemeral=True
            )
            return

        if not self._can_close(interaction, ticket):
            await interaction.response.send_message(
                "❌ Only the ticket author or staff can close this ticket.",
                ephemeral=True,
            )
            return

        # Update the database first so a double-click can't close it twice.
        db.close_ticket(ticket["id"])
        await interaction.response.send_message(
            f"🔒 Ticket closed by {interaction.user.mention}. "
            f"This channel will be deleted in {DELETE_DELAY_SECONDS} seconds."
        )
        logger.info(
            "Ticket %s closed by %s in guild %s",
            ticket["id"], interaction.user.id, interaction.guild_id,
        )

        await asyncio.sleep(DELETE_DELAY_SECONDS)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not delete ticket channel %s: %s", interaction.channel_id, exc)
            await interaction.channel.send(
                "⚠️ I couldn't delete this channel. Please ask a moderator to remove it."
            )

    def _can_close(self, interaction: discord.Interaction, ticket: dict) -> bool:
        """Author, support role members and channel managers may close."""
        user = interaction.user
        if user.id == ticket["author_id"]:
            return True
        if user.guild_permissions.manage_channels:
            return True
        role_id = self.bot.db.get_setting(interaction.guild_id, SUPPORT_ROLE_KEY)
        return bool(role_id) and any(r.id == int(role_id) for r in user.roles)


class Tickets(commands.Cog):
    """Slash commands and views for the support ticket system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Re-register the persistent view so old tickets' buttons keep working.
        self.bot.add_view(CloseTicketView(self.bot))

    # ------------------------------------------------------------------ #
    #  /setup  (admin only)
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="setup", description="Configure the ticket system (admin only)."
    )
    @app_commands.describe(
        category="Category where ticket channels will be created",
        support_role="Role that can see and handle tickets",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)  # hidden from non-admins
    @app_commands.checks.has_permissions(administrator=True)  # enforced at runtime
    async def setup(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        support_role: discord.Role,
    ) -> None:
        me = interaction.guild.me
        if not category.permissions_for(me).manage_channels:
            await interaction.response.send_message(
                f"❌ I need the **Manage Channels** permission in {category.mention}.",
                ephemeral=True,
            )
            return

        db = self.bot.db
        db.set_setting(interaction.guild_id, CATEGORY_KEY, str(category.id))
        db.set_setting(interaction.guild_id, SUPPORT_ROLE_KEY, str(support_role.id))
        await interaction.response.send_message(
            f"✅ Ticket system configured.\n"
            f"**Category:** {category.mention}\n"
            f"**Support role:** {support_role.mention}",
            ephemeral=True,
        )


    # ------------------------------------------------------------------ #
    #  /ticket
    # ------------------------------------------------------------------ #

    @app_commands.command(name="ticket", description="Open a private support ticket.")
    @app_commands.describe(subject="Short description of your issue")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.guild_id, i.user.id))
    async def ticket(
        self,
        interaction: discord.Interaction,
        subject: app_commands.Range[str, 1, 100] = "No subject provided",
    ) -> None:
        guild, user, db = interaction.guild, interaction.user, self.bot.db

        category = guild.get_channel(int(db.get_setting(guild.id, CATEGORY_KEY) or 0))
        role = guild.get_role(int(db.get_setting(guild.id, SUPPORT_ROLE_KEY) or 0))
        if not isinstance(category, discord.CategoryChannel) or role is None:
            await interaction.response.send_message(
                "❌ The ticket system isn't set up yet. Ask an admin to run `/setup`.",
                ephemeral=True,
            )
            return

        # One open ticket per user; recover if its channel was deleted manually.
        existing = db.get_open_ticket(guild.id, user.id)
        if existing:
            channel = guild.get_channel(existing["channel_id"])
            if channel is not None:
                await interaction.response.send_message(
                    f"❌ You already have an open ticket: {channel.mention}",
                    ephemeral=True,
                )
                return
            db.close_ticket(existing["id"])

        # Channel creation can exceed Discord's 3-second response window.
        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            role: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name}"[:90],
                category=category,
                overwrites=overwrites,
                topic=f"Ticket for {user} | {subject}",
                reason=f"Ticket opened by {user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to create channels in the ticket category.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.error("Ticket channel creation failed: %s", exc)
            await interaction.followup.send(
                "❌ Discord rejected the request. Please try again later.", ephemeral=True
            )
            return

        try:
            ticket_id = db.create_ticket(channel.id, guild.id, user.id, subject)
        except Exception:
            # Don't leave an untracked channel behind if the DB write fails.
            await channel.delete(reason="Database error while creating ticket")
            raise

        embed = discord.Embed(
            title=f"Ticket #{ticket_id}",
            description=f"**Subject:** {subject}\n\nSupport will be with you shortly.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Opened by {user}")
        await channel.send(
            content=f"{user.mention} {role.mention}",
            embed=embed,
            view=CloseTicketView(self.bot),
            allowed_mentions=discord.AllowedMentions(users=[user], roles=[role]),
        )
        await interaction.followup.send(
            f"✅ Your ticket has been created: {channel.mention}", ephemeral=True
        )
        logger.info("Ticket %s opened by %s in guild %s", ticket_id, user.id, guild.id)


async def setup(bot: commands.Bot) -> None:
    """Entry point used by ``bot.load_extension``."""
    await bot.add_cog(Tickets(bot))

