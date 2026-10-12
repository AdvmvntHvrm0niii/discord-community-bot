"""Main entry point for the Discord portfolio bot.

Features:
- Loads token and owner ID from ``.env`` via ``python-dotenv``
- Enables the ``members`` and ``message_content`` privileged intents
- Initializes the SQLite ``Database`` and creates tables on startup
- Auto-loads every cog in ``cogs/`` (anything ending in ``.py`` except dunder files)
- Syncs the slash-command tree (globally, or to a single guild if ``GUILD_ID`` is set)
- Provides a global app-command error handler with user-friendly messages

Run with:  python bot.py
"""

import os
import logging
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from database import Database

# --- Paths ----------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COGS_DIR = os.path.join(BASE_DIR, "cogs")

# --- Logging --------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("portfolio_bot")

# --- Environment ----------------------------------------------------------

load_dotenv()


def _int_env(name: str) -> int | None:
    """Read an optional numeric Discord ID from the environment."""
    value = os.getenv(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"{name} must be a numeric Discord ID, got {value!r}")


TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = _int_env("OWNER_ID")
GUILD_ID = _int_env("GUILD_ID")  # optional — instant command sync for development
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")

# --- Intents --------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True          # needed for on_member_join welcome / auto-role
intents.message_content = True  # needed for anti-spam and banned-word detection



# --- Bot class ------------------------------------------------------------


class PortfolioBot(commands.Bot):
    """Custom ``commands.Bot`` subclass with database integration and auto-cog loading."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            owner_id=OWNER_ID,
            description="A portfolio Discord bot built with discord.py",
        )
        self.db = Database(DATABASE_PATH)
        self.db.init_db()
        logger.info("Database initialised at %s", self.db.db_path)

    # -- Lifecycle hooks --------------------------------------------------

    async def setup_hook(self) -> None:
        """Called once before the bot connects — load cogs and sync commands."""
        # Register the global app-command error handler
        self.tree.error(self.on_app_command_error)

        # Load all cogs
        await self._load_cogs()

        # Sync slash commands
        if GUILD_ID:
            # Global commands must be copied to the guild first, otherwise a
            # guild sync would publish nothing.
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild ID %s", GUILD_ID)
        else:
            await self.tree.sync()
            logger.info(
                "Synced commands globally (may take up to 1 hour to propagate)."
            )

    async def _load_cogs(self) -> None:
        """Load every ``.py`` file in the ``cogs/`` directory as an extension."""
        if not os.path.isdir(COGS_DIR):
            logger.warning("Cogs directory not found at %s — skipping.", COGS_DIR)
            return
        for filename in sorted(os.listdir(COGS_DIR)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            ext_name = f"cogs.{filename[:-3]}"
            try:
                await self.load_extension(ext_name)
                logger.info("Loaded cog: %s", ext_name)
            except Exception as exc:
                logger.error("Failed to load cog %s: %s", ext_name, exc, exc_info=True)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guild(s)", len(self.guilds))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info("Joined new guild: %s (ID: %s)", guild.name, guild.id)

    async def close(self) -> None:
        """Shut down — close DB connection before the Discord session."""
        self.db.close()
        await super().close()

    # -- Global app-command error handler ---------------------------------

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Catch-all handler for every slash-command error.

        Logs the full traceback internally and sends a short ephemeral message
        to the user.
        """
        if isinstance(error, app_commands.MissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await self._reply(
                interaction, f"❌ You don't have permission to do that. Missing: `{missing}`"
            )
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await self._reply(interaction, f"❌ I'm missing permissions: `{missing}`")
        elif isinstance(error, app_commands.CommandOnCooldown):
            await self._reply(
                interaction,
                f"❌ Slow down! Try again in {error.retry_after:.1f} seconds.",
            )
        elif isinstance(error, app_commands.CheckFailure):
            await self._reply(interaction, "❌ You can't use this command here.")
        else:
            # Unexpected: log the full traceback, show a generic message
            command = interaction.command.name if interaction.command else "<unknown>"
            logger.error(
                "Unhandled error in /%s: %s", command, error, exc_info=error
            )
            await self._reply(
                interaction, "❌ Something went wrong. Please try again later."
            )

    @staticmethod
    async def _reply(interaction: discord.Interaction, message: str) -> None:
        """Send an ephemeral message, whether or not the interaction was answered."""
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


# -- Entry point ------------------------------------------------------------


def main() -> None:
    """Create the bot instance and start the event loop."""
    if not TOKEN:
        raise ValueError(
            "DISCORD_TOKEN not found. Copy .env.example to .env and add your token."
        )

    bot = PortfolioBot()
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutting down via keyboard interrupt.")
    finally:
        bot.db.close()
        logger.info("Database connection closed.")


if __name__ == "__main__":
    main()
