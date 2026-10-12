"""Giveaways: ``/giveaway start|end|list`` with a persistent Join button.

Giveaways and entries are stored in SQLite. A background task ends giveaways
when their time is up and picks random winners, so giveaways that expire while
the bot is offline are finished on the next start.
"""

import logging
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("portfolio_bot.giveaways")

DURATION_PATTERN = re.compile(r"(\d+)([smhd])", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MIN_DURATION = 10                # seconds
MAX_DURATION = 30 * 86400        # 30 days
CHECK_INTERVAL_SECONDS = 15


def parse_duration(text: str) -> int | None:
    """Convert ``30m`` / ``2h`` / ``1d`` to seconds; ``None`` if invalid."""
    match = DURATION_PATTERN.fullmatch(text.strip())
    if match is None:
        return None
    seconds = int(match[1]) * UNIT_SECONDS[match[2].lower()]
    return seconds if MIN_DURATION <= seconds <= MAX_DURATION else None


def build_embed(
    prize: str,
    winner_count: int,
    end_time: float,
    *,
    ended: bool = False,
    winners: list[int] | None = None,
    entries: int = 0,
) -> discord.Embed:
    """Build the giveaway message embed (running or ended state)."""
    if not ended:
        embed = discord.Embed(
            title=f"🎉 {prize}",
            description="Click **Join Giveaway** below to enter!",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Ends", value=f"<t:{int(end_time)}:R>")
        embed.add_field(name="Winners", value=str(winner_count))
        return embed

    embed = discord.Embed(title=f"🎉 {prize}", color=discord.Color.dark_grey())
    names = " ".join(f"<@{user_id}>" for user_id in winners or []) or "No valid entries"
    embed.add_field(name="Winners", value=names, inline=False)
    embed.add_field(name="Entries", value=str(entries))
    embed.set_footer(text="Giveaway ended")
    return embed


class JoinGiveawayView(discord.ui.View):
    """Persistent Join button; the giveaway is looked up by message id."""

    def __init__(self, bot: commands.Bot, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.join_button.disabled = disabled

    @discord.ui.button(
        label="Join Giveaway",
        style=discord.ButtonStyle.success,
        emoji="🎉",
        custom_id="giveaway:join",
    )
    async def join_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        db = self.bot.db
        giveaway = db.get_giveaway_by_message(interaction.message.id)
        if giveaway is None:
            await interaction.response.send_message(
                "❌ This giveaway no longer exists.", ephemeral=True
            )
        elif giveaway["status"] != "active" or giveaway["end_time"] <= time.time():
            await interaction.response.send_message(
                "⌛ This giveaway has already ended.", ephemeral=True
            )
        elif not db.add_entry(giveaway["id"], interaction.user.id):
            await interaction.response.send_message(
                "You've already entered this giveaway!", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "✅ You're in! Good luck 🍀", ephemeral=True
            )


class Giveaways(commands.Cog):
    """``/giveaway`` command group plus the background finisher task."""

    giveaway = app_commands.Group(
        name="giveaway",
        description="Run giveaways (admin only, except list).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(JoinGiveawayView(self.bot))  # keep buttons alive after restarts
        self.check_giveaways.start()

    async def cog_unload(self) -> None:
        self.check_giveaways.cancel()

    # ------------------------------------------------------------------ #
    #  Ending giveaways
    # ------------------------------------------------------------------ #

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def check_giveaways(self) -> None:
        for giveaway in self.bot.db.get_expired_giveaways():
            try:
                await self._finish(giveaway)
            except Exception:
                logger.exception("Failed to finish giveaway %s", giveaway["id"])

    @check_giveaways.before_loop
    async def _wait_until_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _finish(self, giveaway: dict) -> "list[int] | None":
        """End a giveaway, draw winners and update its message.

        Returns the winner ids, or ``None`` if it had already been ended.
        """
        db = self.bot.db
        if not db.finish_giveaway(giveaway["id"]):
            return None  # another path (e.g. /giveaway end) got there first

        user_ids = [row["user_id"] for row in db.get_entries(giveaway["id"])]
        winners = random.sample(user_ids, min(giveaway["winner_count"], len(user_ids)))

        channel = self.bot.get_channel(giveaway["channel_id"])
        if channel is None:
            logger.warning("Channel for giveaway %s is gone; result not announced", giveaway["id"])
            return winners

        embed = build_embed(
            giveaway["prize"], giveaway["winner_count"], giveaway["end_time"],
            ended=True, winners=winners, entries=len(user_ids),
        )
        try:
            message = await channel.fetch_message(giveaway["message_id"])
            await message.edit(embed=embed, view=JoinGiveawayView(self.bot, disabled=True))
        except discord.NotFound:
            logger.warning("Message for giveaway %s was deleted", giveaway["id"])
        except discord.HTTPException as exc:
            logger.warning("Could not update giveaway %s message: %s", giveaway["id"], exc)

        mentions = " ".join(f"<@{uid}>" for uid in winners)
        text = (
            f"🎉 Congratulations {mentions}! You won **{giveaway['prize']}**!"
            if winners
            else f"😢 Nobody entered the giveaway for **{giveaway['prize']}**."
        )
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException as exc:
            logger.warning("Could not announce giveaway %s: %s", giveaway["id"], exc)
        return winners

    # ------------------------------------------------------------------ #
    #  Commands
    # ------------------------------------------------------------------ #

    @giveaway.command(name="start", description="Start a giveaway (admin only).")
    @app_commands.describe(
        prize="What is being given away",
        duration="How long it runs: 30m, 2h, 1d (10s to 30d)",
        winner_count="Number of winners (1-20)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def start(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winner_count: app_commands.Range[int, 1, 20] = 1,
    ) -> None:
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message(
                "❌ Invalid duration. Use a number plus `s`, `m`, `h` or `d` "
                "(e.g. `30m`, `2h`, `1d`) between 10 seconds and 30 days.",
                ephemeral=True,
            )
            return

        end_time = time.time() + seconds
        await interaction.response.send_message(
            embed=build_embed(prize, winner_count, end_time),
            view=JoinGiveawayView(self.bot),
        )
        message = await interaction.original_response()
        try:
            giveaway_id = self.bot.db.create_giveaway(
                interaction.guild_id, interaction.channel_id, message.id,
                prize, winner_count, end_time,
            )
        except Exception:
            await message.delete()  # don't leave a join button with no record behind it
            raise
        logger.info("Giveaway %s started in guild %s", giveaway_id, interaction.guild_id)

    @giveaway.command(name="end", description="End a giveaway now and pick winners (admin only).")
    @app_commands.describe(giveaway_id="ID shown by /giveaway list")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def end(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        giveaway = self.bot.db.get_giveaway_by_id(giveaway_id)
        if giveaway is None or giveaway["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)
            return
        if giveaway["status"] != "active":
            await interaction.response.send_message(
                "That giveaway has already ended.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        winners = await self._finish(giveaway)
        if winners is None:
            await interaction.followup.send("That giveaway has already ended.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"✅ Giveaway #{giveaway_id} ended with {len(winners)} winner(s).",
                ephemeral=True,
            )

    @giveaway.command(name="list", description="List the running giveaways.")
    async def list_giveaways(self, interaction: discord.Interaction) -> None:
        giveaways = self.bot.db.get_guild_giveaways(interaction.guild_id)
        if not giveaways:
            await interaction.response.send_message("There are no active giveaways.", ephemeral=True)
            return

        embed = discord.Embed(title="🎉 Active giveaways", color=discord.Color.gold())
        for item in giveaways[:25]:
            link = (
                f"https://discord.com/channels/{item['guild_id']}/"
                f"{item['channel_id']}/{item['message_id']}"
            )
            embed.add_field(
                name=f"#{item['id']} — {item['prize']}",
                value=(
                    f"Ends <t:{int(item['end_time'])}:R> • {item['winner_count']} winner(s) • "
                    f"{self.bot.db.count_entries(item['id'])} entries\n[Jump to giveaway]({link})"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Entry point used by ``bot.load_extension``."""
    await bot.add_cog(Giveaways(bot))
