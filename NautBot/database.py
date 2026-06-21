import aiosqlite
import json
import time


class ChatDB:

    def __init__(self, path="nautbot.db"):
        self.path = path


    async def init(self):

        async with aiosqlite.connect(self.path) as db:

            await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                scope TEXT NOT NULL,

                title TEXT NOT NULL,

                context TEXT NOT NULL,

                token_count INTEGER DEFAULT 0,

                created INTEGER NOT NULL,

                updated INTEGER NOT NULL,

                active INTEGER DEFAULT 0
            )
            """)

            await db.commit()



    async def ensure_context(self, scope):

        if await self.get_active(scope) is None:

            await self.new_context(
                scope,
                "New conversation"
            )



    async def new_context(
        self,
        scope,
        title="New conversation"
    ):

        now = int(time.time())

        async with aiosqlite.connect(self.path) as db:

            await db.execute(
                """
                UPDATE conversations
                SET active=0
                WHERE scope=?
                """,
                (scope,)
            )


            await db.execute(
                """
                INSERT INTO conversations
                (
                    scope,
                    title,
                    context,
                    token_count,
                    created,
                    updated,
                    active
                )
                VALUES (?,?,?,?,?,?,1)
                """,
                (
                    scope,
                    title,
                    json.dumps([]),
                    0,
                    now,
                    now
                )
            )


            await db.commit()



    async def get_active(self, scope):

        async with aiosqlite.connect(self.path) as db:

            cur = await db.execute(
                """
                SELECT
                    id,
                    context
                FROM conversations
                WHERE scope=? AND active=1
                LIMIT 1
                """,
                (scope,)
            )

            return await cur.fetchone()



    async def save_context(
        self,
        scope,
        context,
        title=None
    ):

        now = int(time.time())

        async with aiosqlite.connect(self.path) as db:

            if title:

                await db.execute(
                    """
                    UPDATE conversations
                    SET
                    context=?,
                    token_count=?,
                    title=?,
                    updated=?
                    WHERE scope=? AND active=1
                    """,
                    (
                        json.dumps(context),
                        len(context),
                        title,
                        now,
                        scope
                    )
                )

            else:

                await db.execute(
                    """
                    UPDATE conversations
                    SET
                    context=?,
                    token_count=?,
                    updated=?
                    WHERE scope=? AND active=1
                    """,
                    (
                        json.dumps(context),
                        len(context),
                        now,
                        scope
                    )
                )


            await db.commit()



    async def list_contexts(
        self,
        scope,
        offset=0
    ):

        async with aiosqlite.connect(self.path) as db:

            cur = await db.execute(
                """
                SELECT

                    id,
                    title,
                    updated,
                    token_count

                FROM conversations

                WHERE scope=?

                ORDER BY updated DESC

                LIMIT 5 OFFSET ?

                """,
                (
                    scope,
                    offset
                )
            )

            return await cur.fetchall()



    async def activate_context(
        self,
        scope,
        chat_id
    ):

        async with aiosqlite.connect(self.path) as db:

            await db.execute(
                """
                UPDATE conversations
                SET active=0
                WHERE scope=?
                """,
                (scope,)
            )


            await db.execute(
                """
                UPDATE conversations
                SET active=1
                WHERE id=? AND scope=?
                """,
                (
                    chat_id,
                    scope
                )
            )


            await db.commit()