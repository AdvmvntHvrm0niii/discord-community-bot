"""Moderation: anti-spam, banned-word filter and the /warnings command.

Offending messages are deleted and the author receives a recorded warning.
There are deliberately no automatic bans, kicks or timeouts: warnings are
stored so human moderators can decide what to do. Members with Manage Messages
or Administrator are exempt from the filters.
"""

import logging
import re
import time
from collections import defaultdict, deque
from functools import lru_cache

import discord
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("portfolio_bot.moderation")

BANNED_WORDS_KEY = "banned_words"
MAX_BANNED_WORDS = 100

SPAM_MAX_MESSAGES = 5      # more than this many messages...
SPAM_WINDOW_SECONDS = 8    # ...inside this window counts as spam
SPAM_WARN_COOLDOWN = 30    # at most one spam warning per user per 30 seconds
NOTICE_DELETE_AFTER = 10   # seconds before the bot's notice disappears


@lru_cache(maxsize=64)
def compile_pattern(words: tuple[str, ...]) -> re.Pattern:
    """Build one case-insensitive whole-word pattern for a set of words."""
    escaped = "|".join(re.escape(word) for word in words)
    return re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)


class Moderation(commands.Cog):
    """Message filters plus moderator tools."""

    banword = app_commands.Group(
        name="banword",
        description="Manage the banned-word list (admin only).",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._recent: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._last_spam_warning: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        self.cleanup.start()

    async def cog_unload(self) -> None:
        self.cleanup.cancel()

    @tasks.loop(minutes=10)
    async def cleanup(self) -> None:
        """Forget quiet users so the in-memory spam tracking stays small."""
        now = time.monotonic()
        stale = [k for k, q in self._recent.items() if not q or now - q[-1] > SPAM_WINDOW_SECONDS]
        for key in stale:
            del self._recent[key]
        for key in [k for k, t in self._last_spam_warning.items() if now - t > SPAM_WARN_COOLDOWN]:
            del self._last_spam_warning[key]

    # ------------------------------------------------------------------ #
    #  Banned-word storage (guild_settings, one word per line)
    # ------------------------------------------------------------------ #

    def get_banned_words(self, guild_id: int) -> list[str]:
        raw = self.bot.db.get_setting(guild_id, BANNED_WORDS_KEY) or ""
        return [word for word in raw.split("\n") if word]

    def save_banned_words(self, guild_id: int, words: list[str]) -> None:
        self.bot.db.set_setting(guild_id, BANNED_WORDS_KEY, "\n".join(sorted(set(words))))

    # ------------------------------------------------------------------ #
    #  Message filters
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        perms = message.author.guild_permissions
        if perms.administrator or perms.manage_messages:
            return
        if await self._check_banned_words(message):
            return
        await self._check_spam(message)

    async def _check_banned_words(self, message: discord.Message) -> bool:
        words = self.get_banned_words(message.guild.id)
        if not words or not message.content:
            return False
        if not compile_pattern(tuple(sorted(words))).search(message.content):
            return False
        await self._delete_and_warn(message, "Used a banned word")
        return True

    async def _check_spam(self, message: discord.Message) -> None:
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        window = self._recent[key]
        window.append(now)
        while window and now - window[0] > SPAM_WINDOW_SECONDS:
            window.popleft()
        if len(window) <= SPAM_MAX_MESSAGES:
            return

        # Delete every message over the limit, but warn at most once per cooldown
        # so one burst can't pile up dozens of warnings.
        last = self._last_spam_warning.get(key)
        should_warn = last is None or now - last >= SPAM_WARN_COOLDOWN
        if should_warn:
            self._last_spam_warning[key] = now
        await self._delete_and_warn(message, "Spamming messages", warn=should_warn)

    async def _delete_and_warn(
        self, message: discord.Message, reason: str, *, warn: bool = True
    ) -> None:
        guild, author = message.guild, message.author
        try:
            await message.delete()
        except discord.NotFound:
            pass  # already gone
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not delete message in #%s: %s", message.channel, exc)
        if not warn:
            return

        db = self.bot.db
        db.add_warning(guild.id, author.id, self.bot.user.id, reason)
        total = len(db.get_warnings(guild.id, author.id))
        try:
            await message.channel.send(
                f"⚠️ {author.mention} — warning #{total}: {reason}. "
                "Please follow the server rules.",
                delete_after=NOTICE_DELETE_AFTER,
                allowed_mentions=discord.AllowedMentions(users=[author]),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass  # the warning is stored even if the notice can't be posted

    # ------------------------------------------------------------------ #
    #  /warnings  (moderators)
    # ------------------------------------------------------------------ #

    @app_commands.command(name="warnings", description="View a member's warnings.")
    @app_commands.describe(member="Member to look up")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        rows = self.bot.db.get_warnings(interaction.guild_id, member.id)
        if not rows:
            await interaction.response.send_message(
                f"✅ {member.display_name} has no warnings.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Warnings for {member.display_name}",
            description=f"{len(rows)} total (showing the latest 10)",
            color=discord.Color.orange(),
        )
        for row in rows[:10]:
            moderator = (
                "Auto-moderation"
                if row["moderator_id"] == self.bot.user.id
                else f"<@{row['moderator_id']}>"
            )
            embed.add_field(
                name=f"#{row['id']} • <t:{int(row['created_at'])}:R>",
                value=f"{row['reason']}\nBy: {moderator}",
                inline=False,
            )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # ------------------------------------------------------------------ #
    #  /banword add | remove | list  (admin only)
    # ------------------------------------------------------------------ #

    @banword.command(name="add", description="Add a word to the banned list.")
    @app_commands.describe(word="Word to ban (matched as a whole word, ignoring case)")
    @app_commands.checks.has_permissions(administrator=True)
    async def banword_add(
        self, interaction: discord.Interaction, word: app_commands.Range[str, 2, 40]
    ) -> None:
        word = word.strip().lower()
        words = self.get_banned_words(interaction.guild_id)
        if word in words:
            await interaction.response.send_message("That word is already banned.", ephemeral=True)
        elif len(words) >= MAX_BANNED_WORDS:
            await interaction.response.send_message(
                f"❌ The list is full ({MAX_BANNED_WORDS} words).", ephemeral=True
            )
        else:
            self.save_banned_words(interaction.guild_id, words + [word])
            await interaction.response.send_message(f"✅ Banned `{word}`.", ephemeral=True)

    @banword.command(name="remove", description="Remove a word from the banned list.")
    @app_commands.describe(word="Word to unban")
    @app_commands.checks.has_permissions(administrator=True)
    async def banword_remove(
        self, interaction: discord.Interaction, word: app_commands.Range[str, 2, 40]
    ) -> None:
        word = word.strip().lower()
        words = self.get_banned_words(interaction.guild_id)
        if word not in words:
            await interaction.response.send_message("That word isn't banned.", ephemeral=True)
            return
        words.remove(word)
        self.save_banned_words(interaction.guild_id, words)
        await interaction.response.send_message(f"✅ Removed `{word}`.", ephemeral=True)

    @banword.command(name="list", description="Show the banned words.")
    @app_commands.checks.has_permissions(administrator=True)
    async def banword_list(self, interaction: discord.Interaction) -> None:
        words = self.get_banned_words(interaction.guild_id)
        text = ", ".join(f"`{w}`" for w in sorted(words)) if words else "No banned words set."
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Entry point used by ``bot.load_extension``."""
    await bot.add_cog(Moderation(bot))
