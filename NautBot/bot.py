#! /home/craut/miniconda3/envs/discbot/bin/python
import discord
from discord.ext import commands

import aiohttp
import asyncio
import logging
import os
import json
import datetime

from dotenv import load_dotenv

from database import ChatDB


load_dotenv()

logging.basicConfig(
    level=logging.INFO
)

logger = logging.getLogger("NautBot")


class NautBot(commands.Bot):

    def __init__(self):

        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )

        self.db = ChatDB()

        self.ollama = os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434"
        )

        self.model = os.getenv(
            "DEFAULT_MODEL",
            "llama3.1"
        )

        self.context_limit = 131072


    async def setup_hook(self):

        await self.db.init()

        logger.info(
            "Database initialized"
        )


    def scope(self, channel):
        # Direct message
        if isinstance(channel, discord.DMChannel):
            # recipient can be None in some cases
            if channel.recipient:
                return f"dm:{channel.recipient.id}"
            # fallback: use the channel id itself
            return f"dm_channel:{channel.id}"

        # Guild channel
        return (
            f"guild:{channel.guild.id}:"
            f"channel:{channel.id}"
        )


    async def make_title(self, text):
        payload = {
            "model": self.model,
            "prompt":
            """
Create a short chat title.
Maximum 5 words.
Only output the title.

Message:
"""
            + text,
            "stream": False,

            "options": {
                "num_predict": 10,
                "temperature": 0
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.ollama}/api/generate",
                    json=payload,
                    timeout=20
                ) as r:
                    data = await r.json()
                    return data.get(
                        "response",
                        "New chat"
                    ).strip()

        except Exception:
            return "New chat"
    
    async def rag_search(self, query):

        results = await self.searx_search(query)

        if not results:
            return ""


        chunks = []


        async with aiohttp.ClientSession(
            max_field_size=32768,
            max_line_size=32768
        ) as session:


            for result in results[:3]:

                url = result.get("url")

                if not url:
                    continue


                try:

                    async with session.get(
                        url,
                        timeout=10,
                        headers={
                            "User-Agent":
                            "Mozilla/5.0"
                        }
                    ) as r:

                        html = await r.text()


                    from bs4 import BeautifulSoup


                    soup = BeautifulSoup(
                        html,
                        "html.parser"
                    )


                    # remove junk
                    for tag in soup(
                        [
                            "script",
                            "style",
                            "nav",
                            "footer",
                            "header"
                        ]
                    ):
                        tag.decompose()


                    text = soup.get_text(
                        " ",
                        strip=True
                    )


                    chunks.append(
                        text[:2000]
                    )


                except Exception as e:

                    logger.warning(
                        f"Fetch failed {url}: {e}"
                    )


        return "\n\n".join(chunks)

    async def ask(
        self,
        prompt,
        channel
    ):

        scope = self.scope(channel)

        await self.db.ensure_context(
            scope
        )

        chat = await self.db.get_active(
            scope
        )


        context = []

        if chat:

            context = json.loads(
                chat[1]
            )

        # keywords that imply fresh info
        fresh_words = [
            "current",
            "today",
            "latest",
            "status",
            "news",
            "score",
            "weather",
            "recent",
            "now",
            "updates",
            "last week",
            "last month",
            "this year",
            "this week",
            "this month",
            "yesterday",
            "tomorrow",
        ]


        web_context = ""


        if any(
            word in prompt.lower()
            for word in fresh_words
        ):

            results = await self.rag_search(
                prompt
            )


            if results:
                
                if results:
                    web_context = """
Web search results:

""" + results

        # debug to make sure that it actually searched
        logger.info(
            f"Web context for prompt '{prompt}': {web_context}"
        )

        payload = {

            "model": self.model,

            "prompt":
            """
You are NautBot.

You are a private local Discord assistant.

Use web results when provided.
Do not invent current facts.
When you don't know, say you don't know or ask the user for more info.

Rules:
- Keep answers concise.
- Avoid huge paragraphs.
- Use emojis when helpful.
- Discord messages must be readable.

Use this information when relevant:

{web_context}

User:
""".format(web_context=web_context)
            + prompt,

            "context": context,

            "stream": False,

            "options": {

                "temperature": 0.7,

                # Discord friendly
                "num_predict": 700
            }
        }


        try:

            async with aiohttp.ClientSession() as session:

                async with session.post(
                    f"{self.ollama}/api/generate",
                    json=payload,
                    timeout=90
                ) as r:

                    data = await r.json()


            reply = data.get(
                "response",
                "No response"
            )


            new_context = data.get(
                "context",
                []
            )


            title = None

            # Generate title only early in chat
            if len(context) < 100:

                title = await self.make_title(
                    prompt
                )


            await self.db.save_context(
                scope,
                new_context,
                title
            )


            return reply


        except Exception as e:

            logger.error(
                f"Ollama error: {e}"
            )

            return (
                "⚠️ I couldn't reach Ollama."
            )


    async def searx_search(
        self,
        query
    ):

        try:

            async with aiohttp.ClientSession() as session:

                async with session.get(
                    "http://localhost:8080/search",
                    params={
                        "q": query,
                        "format": "json"
                    },
                    timeout=10
                ) as r:

                    data = await r.json()


            return data.get(
                "results",
                []
            )[:5]


        except Exception as e:

            logger.error(
                f"SearXNG error {e}"
            )

            return []



bot = NautBot()



async def send_chunks(
    channel,
    text
):

    while text:

        await channel.send(
            text[:1900]
        )

        text = text[1900:]



@bot.event
async def on_ready():

    logger.info(
        f"Logged in as {bot.user}"
    )



@bot.event
async def on_message(message):

    if message.author.bot:
        return


    ctx = await bot.get_context(
        message
    )


    # Commands first
    if ctx.valid:

        await bot.invoke(ctx)
        return



    # DM = normal chat
    if isinstance(
        message.channel,
        discord.DMChannel
    ):

        async with message.channel.typing():

            reply = await bot.ask(
                message.content,
                message.channel
            )

            await send_chunks(
                message.channel,
                reply
            )

        return



    # Server mention
    if bot.user in message.mentions:

        prompt = message.clean_content.replace(
            f"@{bot.user.display_name}",
            ""
        ).strip()


        async with message.channel.typing():

            reply = await bot.ask(
                prompt,
                message.channel
            )

            await send_chunks(
                message.channel,
                reply
            )



@bot.command()
async def clear(ctx):

    await bot.db.new_context(
        bot.scope(ctx.channel),
        "Fresh chat"
    )

    await ctx.reply(
        "🧹 Started a new conversation"
    )



@bot.command()
async def context(ctx):

    chat = await bot.db.get_active(
        bot.scope(ctx.channel)
    )


    used = 0

    if chat:

        used = len(
            json.loads(chat[1])
        )


    percent = (
        used /
        bot.context_limit *
        100
    )


    await ctx.reply(
        f"🧠 Context: "
        f"{used}/{bot.context_limit} "
        f"tokens ({percent:.2f}%)"
    )



@bot.command()
async def chats(ctx):

    scope = bot.scope(
        ctx.channel
    )

    rows = await bot.db.list_contexts(
        scope
    )


    if not rows:

        await ctx.reply(
            "No saved chats."
        )

        return


    emojis = [
        "1️⃣",
        "2️⃣",
        "3️⃣",
        "4️⃣",
        "5️⃣"
    ]


    lines = [
        "📚 **Saved contexts**",
        "",
        "```\n",
        "# Summary                 Updated       Context",
        "------------------------------------------------"
    ]


    for i,row in enumerate(rows):

        cid,title,updated,tokens = row

        date = datetime.datetime.fromtimestamp(
            updated
        ).strftime(
            "%m/%d %H:%M"
        )


        pct = (
            tokens /
            bot.context_limit *
            100
        )


        lines.append(
            f"{i+1} "
            f"{title[:22]:22} "
            f"{date:12} "
            f"{pct:5.1f}%"
        )


    lines.append(
        "```\n"
        "React to load a context."
    )


    msg = await ctx.reply(
        "\n".join(lines)
    )


    for e in emojis[:len(rows)]:

        await msg.add_reaction(e)



    def check(
        reaction,
        user
    ):

        return (
            user == ctx.author
            and str(reaction.emoji) in emojis
        )


    try:

        reaction,user = await bot.wait_for(
            "reaction_add",
            timeout=60,
            check=check
        )


        index = emojis.index(
            str(reaction.emoji)
        )


        await bot.db.activate_context(
            scope,
            rows[index][0]
        )


        await ctx.send(
            "✅ Loaded conversation"
        )


    except asyncio.TimeoutError:

        pass



@bot.command(name="search")
async def search_cmd(
    ctx,
    *,
    query
):

    results = await bot.searx_search(
        query
    )


    if not results:

        await ctx.reply(
            "No results."
        )

        return


    text = "\n\n".join(
        [
            f"**{r['title']}**\n{r['url']}"
            for r in results
        ]
    )


    await send_chunks(
        ctx.channel,
        text
    )



@bot.command(name="help")
async def help_cmd(ctx):

    await ctx.reply(
        """
🤖 NautBot

DM:
Just message me normally

Commands:
!clear
!context
!chats
!search <query>

Server:
Mention me to chat
"""
    )



if __name__ == "__main__":

    token = os.getenv(
        "DISCORD_TOKEN"
    )

    if not token:

        raise RuntimeError(
            "Missing DISCORD_TOKEN"
        )


    bot.run(token)