"""SQLite database layer for the Discord bot.

Provides a single ``Database`` class that manages the connection and offers
small, focused helper methods for every storage domain:
tickets, warnings, giveaways (+ entries), and member activity.

Uses only the standard-library ``sqlite3`` module — no external dependencies.
All queries use parameterized placeholders to prevent SQL injection.
"""

import os
import sqlite3
import time


class Database:
    """Thin wrapper around the SQLite connection with schema initialization."""

    def __init__(self, db_path: str = "data/bot.db"):
        """Store the database path and open a connection.

        Parameters
        ----------
        db_path:
            Path to the SQLite file. Parent directories are created if missing.
            Defaults to ``data/bot.db`` which is git-ignored.
        """
        self.db_path = db_path
        self._conn = None
        self._connect()

    # ------------------------------------------------------------------ #
    #  Connection management
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        """Open (or return existing) the SQLite connection.

        ``check_same_thread=False`` is safe here because discord.py runs its
        event loop in a single thread; this flag simply avoids spurious errors
        when the connection is used from async callbacks.
        """
        if self._conn is None:
            directory = os.path.dirname(self.db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row  # access columns by name
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the active connection, reconnecting if necessary."""
        if self._conn is None:
            self._connect()
        return self._conn

    def close(self) -> None:
        """Close the underlying SQLite connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ #
    #  Schema initialization
    # ------------------------------------------------------------------ #

    def init_db(self) -> None:
        """Create the SQLite file (if it does not exist) and all tables."""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id  INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                author_id   INTEGER NOT NULL,
                subject     TEXT,
                status      TEXT NOT NULL DEFAULT 'open',
                created_at  REAL NOT NULL,
                closed_at   REAL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason       TEXT NOT NULL,
                created_at   REAL NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaways (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                channel_id   INTEGER NOT NULL,
                message_id   INTEGER NOT NULL,
                prize        TEXT NOT NULL,
                winner_count INTEGER NOT NULL DEFAULT 1,
                end_time     REAL NOT NULL,
                status       TEXT NOT NULL DEFAULT 'active',
                created_at   REAL NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id  INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                created_at   REAL NOT NULL,
                UNIQUE (giveaway_id, user_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS member_activity (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id          INTEGER NOT NULL,
                user_id           INTEGER NOT NULL,
                message_count     INTEGER NOT NULL DEFAULT 0,
                last_message_time REAL,
                updated_at        REAL NOT NULL,
                UNIQUE (guild_id, user_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            )
            """
        )

        self.conn.commit()

    # ------------------------------------------------------------------ #
    #  Tickets
    # ------------------------------------------------------------------ #

    def create_ticket(
        self,
        channel_id: int,
        guild_id: int,
        author_id: int,
        subject: str | None = None,
    ) -> int:
        """Insert a new ticket and return its row id."""
        now = time.time()
        cursor = self.conn.execute(
            """
            INSERT INTO tickets (channel_id, guild_id, author_id, subject, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (channel_id, guild_id, author_id, subject, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def close_ticket(self, ticket_id: int) -> None:
        """Mark a ticket as closed by its database id."""
        self.conn.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
            (time.time(), ticket_id),
        )
        self.conn.commit()

    def get_ticket_by_channel(self, channel_id: int) -> dict | None:
        """Return a single ticket row (as a dict) by its channel id."""
        cursor = self.conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_open_ticket(self, guild_id: int, author_id: int) -> dict | None:
        """Return an open ticket owned by *author_id* in *guild_id*, if any."""
        cursor = self.conn.execute(
            """
            SELECT * FROM tickets
            WHERE guild_id = ? AND author_id = ? AND status = 'open'
            """,
            (guild_id, author_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


    # ------------------------------------------------------------------ #
    #  Warnings
    # ------------------------------------------------------------------ #

    def add_warning(
        self, guild_id: int, user_id: int, moderator_id: int, reason: str
    ) -> int:
        """Insert a warning row and return its row id."""
        cursor = self.conn.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, moderator_id, reason, time.time()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_warnings(self, guild_id: int, user_id: int) -> list[dict]:
        """Return all warnings for a user in a guild (most-recent first)."""
        cursor = self.conn.execute(
            """
            SELECT * FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
            """,
            (guild_id, user_id),
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_warnings(self, guild_id: int, user_id: int) -> int:
        """Remove all warnings for a user in a guild; return rows deleted."""
        cursor = self.conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        self.conn.commit()
        return cursor.rowcount


    # ------------------------------------------------------------------ #
    #  Giveaways
    # ------------------------------------------------------------------ #

    def create_giveaway(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        prize: str,
        winner_count: int,
        end_time: float,
    ) -> int:
        """Insert a new giveaway and return its row id."""
        now = time.time()
        cursor = self.conn.execute(
            """
            INSERT INTO giveaways
                (guild_id, channel_id, message_id, prize, winner_count, end_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, message_id, prize, winner_count, end_time, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def end_giveaway(self, giveaway_id: int, status: str = "ended") -> None:
        """Update a giveaway's status (e.g. 'ended', 'cancelled')."""
        self.conn.execute(
            "UPDATE giveaways SET status = ? WHERE id = ?",
            (status, giveaway_id),
        )
        self.conn.commit()

    def get_active_giveaways(self) -> list[dict]:
        """Return all giveaways whose end_time has not yet passed."""
        now = time.time()
        cursor = self.conn.execute(
            "SELECT * FROM giveaways WHERE status = 'active' AND end_time > ?",
            (now,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_expired_giveaways(self) -> list[dict]:
        """Return active giveaways whose end_time has passed."""
        now = time.time()
        cursor = self.conn.execute(
            "SELECT * FROM giveaways WHERE status = 'active' AND end_time <= ?",
            (now,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_giveaway_by_id(self, giveaway_id: int) -> dict | None:
        """Return a single giveaway row by its database id."""
        cursor = self.conn.execute(
            "SELECT * FROM giveaways WHERE id = ?",
            (giveaway_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_giveaway_by_message(self, message_id: int) -> dict | None:
        """Return a single giveaway row by its Discord message id."""
        cursor = self.conn.execute(
            "SELECT * FROM giveaways WHERE message_id = ?",
            (message_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None



    # ------------------------------------------------------------------ #
    #  Giveaway entries
    # ------------------------------------------------------------------ #

    def add_entry(self, giveaway_id: int, user_id: int) -> bool:
        """Insert an entry; return ``True`` if added, ``False`` if duplicate."""
        try:
            self.conn.execute(
                """
                INSERT INTO giveaway_entries (giveaway_id, user_id, created_at)
                VALUES (?, ?, ?)
                """,
                (giveaway_id, user_id, time.time()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            # UNIQUE constraint on (giveaway_id, user_id)
            return False

    def get_entries(self, giveaway_id: int) -> list[dict]:
        """Return all entries for a giveaway."""
        cursor = self.conn.execute(
            "SELECT * FROM giveaway_entries WHERE giveaway_id = ? ORDER BY created_at",
            (giveaway_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_entries(self, giveaway_id: int) -> int:
        """Remove all entries for a giveaway; return rows deleted."""
        cursor = self.conn.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ?",
            (giveaway_id,),
        )
        self.conn.commit()
        return cursor.rowcount


    # ------------------------------------------------------------------ #
    #  Member activity
    # ------------------------------------------------------------------ #

    def record_message(self, guild_id: int, user_id: int) -> None:
        """Increment a user's message count in a guild (upsert)."""
        now = time.time()
        self.conn.execute(
            """
            INSERT INTO member_activity (guild_id, user_id, message_count, last_message_time, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                message_count     = message_count + 1,
                last_message_time = ?,
                updated_at        = ?
            """,
            (guild_id, user_id, now, now, now, now),
        )
        self.conn.commit()

    def get_message_count(self, guild_id: int, user_id: int) -> int:
        """Return the message count for a user in a guild (0 if no rows)."""
        cursor = self.conn.execute(
            "SELECT message_count FROM member_activity WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_top_members(self, guild_id: int, limit: int = 10) -> list[dict]:
        """Return the top *limit* members by message count."""
        cursor = self.conn.execute(
            "SELECT * FROM member_activity WHERE guild_id = ? ORDER BY message_count DESC LIMIT ?",
            (guild_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def reset_activity(self, days: int = 30) -> None:
        """Zero out message counts for members inactive longer than *days*.

        Useful for periodic leaderboard resets (called from a bot task loop).
        """
        cutoff = time.time() - (days * 86400)
        self.conn.execute(
            "UPDATE member_activity SET message_count = 0 WHERE last_message_time < ? OR last_message_time IS NULL",
            (cutoff,),
        )
        self.conn.commit()


    # ------------------------------------------------------------------ #
    #  Guild settings (key/value per guild)
    # ------------------------------------------------------------------ #

    def set_setting(self, guild_id: int, key: str, value: str) -> None:
        """Create or update a per-guild setting."""
        self.conn.execute(
            """
            INSERT INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value
            """,
            (guild_id, key, str(value)),
        )
        self.conn.commit()

    def get_setting(self, guild_id: int, key: str) -> str | None:
        """Return a per-guild setting value, or ``None`` if unset."""
        cursor = self.conn.execute(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
            (guild_id, key),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ #
    #  Aggregates for /stats and the giveaway commands
    # ------------------------------------------------------------------ #

    def get_ticket_counts(self, guild_id: int) -> tuple[int, int]:
        """Return ``(total, open)`` ticket counts for a guild."""
        row = self.conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(status = 'open'), 0)
            FROM tickets WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()
        return row[0], row[1]

    def count_warnings(self, guild_id: int) -> int:
        """Return the total number of warnings issued in a guild."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return row[0]

    def count_active_giveaways(self, guild_id: int) -> int:
        """Return how many giveaways in a guild are still running."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM giveaways WHERE guild_id = ? AND status = 'active'",
            (guild_id,),
        ).fetchone()
        return row[0]

    def get_guild_giveaways(self, guild_id: int) -> list[dict]:
        """Return a guild's running giveaways, soonest-ending first."""
        cursor = self.conn.execute(
            """
            SELECT * FROM giveaways
            WHERE guild_id = ? AND status = 'active'
            ORDER BY end_time
            """,
            (guild_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def finish_giveaway(self, giveaway_id: int) -> bool:
        """Atomically mark a giveaway ended.

        Returns ``True`` only for the caller that actually changed the status,
        so a giveaway can never be ended (and announced) twice.
        """
        cursor = self.conn.execute(
            "UPDATE giveaways SET status = 'ended' WHERE id = ? AND status = 'active'",
            (giveaway_id,),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def count_entries(self, giveaway_id: int) -> int:
        """Return the number of entries for a giveaway."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?",
            (giveaway_id,),
        ).fetchone()
        return row[0]

