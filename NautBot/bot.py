#! /home/craut/miniconda3/envs/discbot/bin/python
"""
NautBot — Discord assistant.

Chat is served by a local llama.cpp server (OpenAI-compatible
`/v1/chat/completions`), defined in docker-compose.yml as the
`llamacpp-local` service. Live web search is delegated entirely to
Vane (`vane` service), which does its own query classification, page
fetching, and cited-answer synthesis — this file does not re-implement
any of that.

Run manually (not part of docker-compose):
    python bot.py

Required environment variables (.env):
    DISCORD_TOKEN               Discord bot token.

Optional environment variables — defaults assume you're running this
script on the same host as docker-compose, hitting its mapped ports:
    LLAMACPP_BASE_URL           default http://localhost:11434
    VANE_BASE_URL                default http://localhost:3001
    MODEL_NAME                   default "bonsai-27b" (matches the
                                  --alias set in docker-compose.yml)
    MODEL_TRAINING_CUTOFF        e.g. "March 2024". Used so the
                                  needs-search classifier can reason
                                  about staleness instead of guessing.
                                  Set this to your actual model's
                                  training cutoff.
    CONTEXT_LIMIT                default 131072

Vane setup (one-time, done in Vane's own web UI at VANE_BASE_URL, not
in this script): add an AI provider pointing at the llama.cpp server's
OpenAI-compatible endpoint, and an embedding provider (Vane's
/api/search requires one even for web-only search).

If you already know the providerId/key pair (e.g. from Vane's
GET /api/providers, or from a working request like Vane's own
example client), set these to skip auto-detection entirely:
    VANE_CHAT_PROVIDER_ID / VANE_CHAT_MODEL_KEY
    VANE_EMBEDDING_PROVIDER_ID / VANE_EMBEDDING_MODEL_KEY

Otherwise, at startup this bot calls GET /api/providers and tries to
auto-match your chat/embedding models by key/provider name. If it
picks the wrong one and you'd rather narrow the search than hardcode
IDs, use:
    VANE_CHAT_PROVIDER_NAME      provider name as shown in Vane's UI
    VANE_CHAT_MODEL_KEY          exact model key from /api/providers
    VANE_EMBEDDING_PROVIDER_NAME
"""

import discord
from discord.ext import commands

import aiohttp
import asyncio
import logging
import os
import json
import datetime
import time

from dotenv import load_dotenv

from database import ChatDB


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NautBot")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLAMACPP_BASE_URL = os.getenv(
    "LLAMACPP_BASE_URL", "http://localhost:11434"
).rstrip("/")

VANE_BASE_URL = os.getenv(
    "VANE_BASE_URL", "http://localhost:3001"
).rstrip("/")

MODEL_NAME = os.getenv("MODEL_NAME", "bonsai-27b")

MODEL_TRAINING_CUTOFF = os.getenv(
    "MODEL_TRAINING_CUTOFF",
    "unknown (set MODEL_TRAINING_CUTOFF in .env for accurate "
    "search decisions)"
)

CONTEXT_LIMIT = int(os.getenv("CONTEXT_LIMIT", "131072"))

# Fast path: if you already know these (e.g. from Vane's own working
# example client), set them and auto-detection is skipped entirely.
VANE_CHAT_PROVIDER_ID = os.getenv("VANE_CHAT_PROVIDER_ID")
VANE_EMBEDDING_PROVIDER_ID = os.getenv("VANE_EMBEDDING_PROVIDER_ID")

# Used either as exact-match keys alongside the IDs above, or as
# hints for auto-detection via GET /api/providers if the IDs aren't set.
VANE_CHAT_PROVIDER_NAME = os.getenv("VANE_CHAT_PROVIDER_NAME")
VANE_CHAT_MODEL_KEY = os.getenv("VANE_CHAT_MODEL_KEY")
VANE_EMBEDDING_PROVIDER_NAME = os.getenv("VANE_EMBEDDING_PROVIDER_NAME")
VANE_EMBEDDING_MODEL_KEY = os.getenv("VANE_EMBEDDING_MODEL_KEY")

# Confirmed working value from a known-good Vane request.
VANE_OPTIMIZATION_MODE = os.getenv("VANE_OPTIMIZATION_MODE", "speed")

# How many prior turns (user+assistant messages, not pairs) to hand to
# Vane as conversation history for a search query. Kept small since
# Vane does its own reasoning over this, not full chat continuation.
VANE_HISTORY_TURNS = 10

# The number of maximum tokens to use on any request (includes reasoning and output)
MAX_TOKENS = int(os.getenv("MAX_TOKENS", CONTEXT_LIMIT))

SYSTEM_PROMPT = (
    "You are NautBot, a private local Discord assistant.\n"
    "Keep answers concise, avoid huge paragraphs, and use emojis "
    "sparingly when they genuinely help. Format for Discord "
    "(markdown, short lines). When you don't know something, say so "
    "or ask the user for more info rather than guessing."
)


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
        self.session = None  # aiohttp.ClientSession, created in setup_hook

        self.context_limit = CONTEXT_LIMIT

        # --- debug mode ---
        # per-scope toggle (DMs and each guild channel track this
        # independently). In-memory only: resets on bot restart,
        # since this is meant as a live "watch it work" session
        # tool, not a persisted setting.
        self.debug_scopes = {}

        # --- Vane provider wiring, resolved at startup ---
        self.vane_chat_model = None       # {"providerId": ..., "key": ...}
        self.vane_embedding_model = None  # {"providerId": ..., "key": ...}
        self.vane_ready = False


    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    async def setup_hook(self):

        self.session = aiohttp.ClientSession()

        await self.db.init()
        logger.info("Database initialized")

        await self.resolve_vane_models()


    async def close(self):

        if self.session:
            await self.session.close()

        await super().close()


    # -----------------------------------------------------------------
    # Debug breadcrumbs / scoping
    # -----------------------------------------------------------------

    def is_debug(self, scope):
        return self.debug_scopes.get(scope, False)


    def toggle_debug(self, scope):
        new_state = not self.is_debug(scope)
        self.debug_scopes[scope] = new_state
        return new_state


    async def dbg(self, channel, text):
        """
        Always logs a pipeline-stage breadcrumb to the console
        (logger.info), regardless of debug mode -- this is what lets
        you watch decisions from the terminal without touching
        Discord at all.

        Additionally sends the same text to the channel as a quote
        block, but only if debug mode is on for that channel's
        scope, and only if a channel was given.
        """

        scope = self.scope(channel) if channel else "no-channel"
        logger.info(f"[{scope}] {text}")

        if channel is None:
            return

        if not self.is_debug(scope):
            return

        quoted = "\n".join(
            f"> {line}" for line in text.splitlines()
        ) or "> (empty)"

        for i in range(0, len(quoted), 1900):
            try:
                await channel.send(quoted[i:i + 1900])
            except Exception as e:
                logger.warning(f"Debug send failed: {e}")


    def scope(self, channel):
        # Direct message
        if isinstance(channel, discord.DMChannel):
            # recipient can be None in some cases
            if channel.recipient:
                return f"dm:{channel.recipient.id}"
            # fallback: use the channel id itself
            return f"dm_channel:{channel.id}"

        # Guild channel
        return f"guild:{channel.guild.id}:channel:{channel.id}"


    def _load_history(self, context_json):
        """
        Parses a conversation's stored `context` column into our
        [{"role": ..., "content": ...}, ...] message format.

        Older databases (from before the llama.cpp/Vane rewrite)
        stored Ollama's opaque token-ID array here instead -- those
        aren't usable with the new backend, so treat anything that
        isn't a list of role/content dicts as an empty history
        rather than crashing on it.
        """

        try:
            data = json.loads(context_json)
        except (TypeError, json.JSONDecodeError):
            return []

        if not isinstance(data, list):
            return []

        if not all(
            isinstance(m, dict) and "role" in m and "content" in m
            for m in data
        ):
            return []

        return data


    # -----------------------------------------------------------------
    # llama.cpp server client (chat + tokenize)
    # -----------------------------------------------------------------

    async def llama_chat(
        self,
        messages,
        temperature=0.7,
        max_tokens=MAX_TOKENS,
        timeout=90,
        label="chat"
    ):
        """
        Calls the llama.cpp server's OpenAI-compatible chat
        completions endpoint. Returns "" on any failure rather than
        raising, so callers can decide how to degrade.

        `label` is purely for logging (e.g. "classify", "reply",
        "title", "condense") so the console log shows which stage
        each call belongs to.
        """

        # llama.cpp's validator rejects any non-assistant message
        # with empty content (not just a missing key) -- filter
        # defensively rather than letting the whole request 400.
        messages = [
            m for m in messages
            if m.get("role") == "assistant" or (m.get("content") or "").strip()
        ]

        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        started = time.perf_counter()

        try:
            async with self.session.post(
                f"{LLAMACPP_BASE_URL}/v1/chat/completions",
                json=payload,
                timeout=timeout
            ) as r:

                body_text = await r.text()
                elapsed = time.perf_counter() - started

                if r.status != 200:
                    logger.warning(
                        f"[llama:{label}] {r.status} after "
                        f"{elapsed:.2f}s for "
                        f"{LLAMACPP_BASE_URL}/v1/chat/completions: "
                        f"{body_text[:500]} | sent roles/lengths: "
                        + ", ".join(
                            f"{m.get('role')}={len(m.get('content') or '')}"
                            for m in messages
                        )
                    )
                    return ""

                data = json.loads(body_text)

            if "choices" not in data:
                logger.warning(
                    f"[llama:{label}] response had no 'choices' key "
                    f"after {elapsed:.2f}s. Full response: "
                    f"{body_text[:500]}"
                )
                return ""

            content = data["choices"][0]["message"]["content"].strip()

            logger.info(
                f"[llama:{label}] {elapsed:.2f}s, "
                f"{len(content)} chars out"
            )

            return content

        except Exception as e:
            elapsed = time.perf_counter() - started
            logger.warning(
                f"[llama:{label}] call failed after {elapsed:.2f}s: {e}"
            )
            return ""


    async def count_tokens(self, text):
        """
        Uses llama.cpp server's /tokenize endpoint for an accurate
        token count (used for the !context / !chats displays).
        Falls back to a rough chars/4 estimate if the endpoint is
        unreachable, so those commands never hard-fail.
        """

        if not text:
            return 0

        try:
            async with self.session.post(
                f"{LLAMACPP_BASE_URL}/tokenize",
                json={"content": text},
                timeout=10
            ) as r:
                data = await r.json()

            return len(data.get("tokens", []))

        except Exception as e:
            logger.warning(f"Tokenize call failed, estimating: {e}")
            return len(text) // 4


    async def make_title(self, text):

        response = await self.llama_chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Create a short chat title. Maximum 5 words. "
                        "Only output the title."
                    )
                },
                {"role": "user", "content": text}
            ],
            temperature=0,
            max_tokens=MAX_TOKENS,
            timeout=30,
            label="title"
        )

        return response or "New chat"


    # -----------------------------------------------------------------
    # Vane wiring
    # -----------------------------------------------------------------

    async def resolve_vane_models(self):
        """
        Fetches Vane's configured providers and picks a chat model
        and an embedding model to use for /api/search calls. Vane
        itself must already have these providers configured (via its
        own setup UI) — this just figures out which providerId/key
        pair to send.

        Selection order per kind (chat/embedding):
          1. exact VANE_*_MODEL_KEY match (optionally narrowed to
             VANE_*_PROVIDER_NAME)
          2. a model key containing MODEL_NAME (chat only — catches
             the common case where Vane's provider wraps the same
             llama.cpp model with a matching alias)
          3. the first available model of that kind, anywhere

        Leaves self.vane_ready = False (search disabled, bot still
        chats normally) if no usable models are found at all.
        """

        # Fast path: both IDs hardcoded, no need to call Vane yet.
        if (
            VANE_CHAT_PROVIDER_ID and VANE_CHAT_MODEL_KEY
            and VANE_EMBEDDING_PROVIDER_ID and VANE_EMBEDDING_MODEL_KEY
        ):

            self.vane_chat_model = {
                "providerId": VANE_CHAT_PROVIDER_ID,
                "key": VANE_CHAT_MODEL_KEY
            }
            self.vane_embedding_model = {
                "providerId": VANE_EMBEDDING_PROVIDER_ID,
                "key": VANE_EMBEDDING_MODEL_KEY
            }
            self.vane_ready = True

            logger.info(
                "Vane ready -- using hardcoded provider IDs from env"
            )
            return

        try:
            async with self.session.get(
                f"{VANE_BASE_URL}/api/providers",
                timeout=10
            ) as r:
                data = await r.json()

        except Exception as e:
            logger.warning(
                f"Could not reach Vane at {VANE_BASE_URL} "
                f"({e}) -- live search will be unavailable."
            )
            return

        providers = data.get("providers", [])

        self.vane_chat_model, chat_provider = self._pick_vane_model(
            providers,
            "chatModels",
            VANE_CHAT_PROVIDER_NAME,
            VANE_CHAT_MODEL_KEY,
            alias_hint=MODEL_NAME
        )

        self.vane_embedding_model, embed_provider = self._pick_vane_model(
            providers,
            "embeddingModels",
            VANE_EMBEDDING_PROVIDER_NAME,
            VANE_EMBEDDING_MODEL_KEY,
            alias_hint=None
        )

        if self.vane_chat_model and self.vane_embedding_model:

            self.vane_ready = True

            logger.info(
                f"Vane ready -- chat: {chat_provider}/"
                f"{self.vane_chat_model['key']}, embedding: "
                f"{embed_provider}/{self.vane_embedding_model['key']}"
            )

        else:

            logger.warning(
                "Vane has no usable chat+embedding model pair "
                "configured -- live search will be unavailable until "
                "you add providers in Vane's UI (or set "
                "VANE_CHAT_MODEL_KEY / VANE_EMBEDDING_MODEL_KEY)."
            )


    def _pick_vane_model(
        self,
        providers,
        model_field,
        provider_name_override,
        model_key_override,
        alias_hint
    ):

        # 1. exact key match, optionally narrowed by provider name
        if model_key_override:
            for p in providers:
                if provider_name_override and p["name"] != provider_name_override:
                    continue
                for m in p.get(model_field, []):
                    if m["key"] == model_key_override:
                        return (
                            {"providerId": p["id"], "key": m["key"]},
                            p["name"]
                        )

        # 2. alias-hint match (chat models only, in practice)
        if alias_hint:
            for p in providers:
                if provider_name_override and p["name"] != provider_name_override:
                    continue
                for m in p.get(model_field, []):
                    if alias_hint.lower() in m["key"].lower():
                        return (
                            {"providerId": p["id"], "key": m["key"]},
                            p["name"]
                        )

        # 3. first available, honoring a provider-name pin if given
        for p in providers:
            if provider_name_override and p["name"] != provider_name_override:
                continue
            models = p.get(model_field, [])
            if models:
                return (
                    {"providerId": p["id"], "key": models[0]["key"]},
                    p["name"]
                )

        return None, None


    async def needs_search(self, prompt, channel):
        """
        Asks the model directly whether this message needs a live
        web search, grounded in its actual training cutoff and the
        real current time (rather than the model guessing at both).

        Fails closed (returns False) on any error/ambiguous output,
        since a missed search is much cheaper than a needless one.
        """

        now = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%A, %B %d, %Y %H:%M UTC"
        )

        classify_prompt = (
            "You are a binary classifier for a Discord chat bot.\n"
            f"Your training data cutoff is: {MODEL_TRAINING_CUTOFF}\n"
            f"The current real-world date/time is: {now}\n\n"
            "Decide if answering the user's message requires a live "
            "web search: current events, prices, scores, weather, "
            "recent releases, or any fact that may have changed "
            "since your training cutoff or that you would not "
            "reliably know without looking it up.\n"
            "Do NOT search for general knowledge, definitions, math, "
            "code help, opinions, or casual conversation.\n\n"
            "Answer with exactly one word: YES or NO.\n\n"
            f"Message: {prompt}\n"
            "Answer:"
        )

        response = await self.llama_chat(
            messages=[{"role": "user", "content": classify_prompt}],
            temperature=0,
            max_tokens=MAX_TOKENS,
            timeout=35,
            label="classify"
        )

        decision = response.strip().upper().startswith("YES")

        await self.dbg(
            channel,
            f"🔍 needs_search? raw='{response.strip()}' -> {decision}"
        )

        return decision


    def history_to_vane_pairs(self, history):
        """
        Converts our stored [{"role": "user"/"assistant", "content":
        ...}, ...] messages into Vane's [["human"/"assistant", ...],
        ...] pairs, keeping only the most recent VANE_HISTORY_TURNS.
        """

        role_map = {"user": "human", "assistant": "assistant"}

        pairs = [
            [role_map[m["role"]], m["content"]]
            for m in history
            if m.get("role") in role_map
        ]

        return pairs[-VANE_HISTORY_TURNS:]


    async def vane_search(self, query, history, channel):
        """
        Calls Vane's /api/search with the resolved chat/embedding
        models. Returns {"message": ..., "sources": [...]} on
        success, or None on failure/unavailability -- callers should
        fall back to a normal (non-search) reply in that case.
        """

        if not self.vane_ready:
            await self.dbg(
                channel,
                "🚫 Vane not configured, skipping search"
            )
            return None

        payload = {
            "chatModel": self.vane_chat_model,
            "embeddingModel": self.vane_embedding_model,
            "optimizationMode": VANE_OPTIMIZATION_MODE,
            "sources": ["web"],
            "query": query,
            "history": self.history_to_vane_pairs(history),
            "stream": False
        }

        await self.dbg(
            channel,
            f"🌐 calling Vane: query='{query[:120]}', "
            f"{len(payload['history'])} history pair(s)"
        )

        started = time.perf_counter()

        try:
            async with self.session.post(
                f"{VANE_BASE_URL}/api/search",
                json=payload,
                timeout=1000
            ) as r:

                elapsed = time.perf_counter() - started

                if r.status != 200:
                    body = await r.text()
                    await self.dbg(
                        channel,
                        f"❌ Vane returned {r.status} after "
                        f"{elapsed:.2f}s: {body[:300]}"
                    )
                    return None

                data = await r.json()

        except Exception as e:
            elapsed = time.perf_counter() - started
            logger.warning(f"Vane search failed after {elapsed:.2f}s: {e}")
            await self.dbg(
                channel, f"❌ Vane request failed after {elapsed:.2f}s: {e}"
            )
            return None

        sources = data.get("sources", [])

        await self.dbg(
            channel,
            f"🌐 Vane answered in {elapsed:.2f}s using "
            f"{len(sources)} source(s):\n"
            + "\n".join(
                f"  - {s.get('metadata', {}).get('url', '?')}"
                for s in sources
            )
        )

        return data


    async def condense_for_discord(self, query, vane_message, sources, channel):
        """
        Vane's answers are written for a web UI and tend to be long.
        This rewrites one down into something that reads well as a
        Discord message, while keeping citations to only the sources
        Vane actually returned (formatted as Discord markdown links).
        """

        source_list = "\n".join(
            f"- {s.get('metadata', {}).get('title', 'source')}: "
            f"{s.get('metadata', {}).get('url', '')}"
            for s in sources
        ) or "(no sources)"

        prompt = (
            "Rewrite the RESEARCH ANSWER below as a concise Discord "
            "message.\n"
            "- A few short sentences or a tight list -- not a "
            "report.\n"
            "- Keep every factual claim, cut filler and repeated "
            "framing.\n"
            "- Cite sources inline as Discord markdown links, "
            "like [short label](url), using ONLY urls from the "
            "AVAILABLE SOURCES list below. Never invent a url.\n"
            "- No trailing 'Sources:' section -- citations go "
            "inline.\n"
            "- At most 1-2 emojis, only if they genuinely help.\n\n"
            f"Original question: {query}\n\n"
            f"RESEARCH ANSWER:\n{vane_message}\n\n"
            f"AVAILABLE SOURCES:\n{source_list}\n\n"
            "Discord message:"
        )

        condensed = await self.llama_chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=MAX_TOKENS,
            timeout=400,
            label="condense"
        )

        await self.dbg(
            channel,
            f"✂️ condensed vane answer: {len(vane_message)} chars -> "
            f"{len(condensed)} chars"
        )

        return condensed or vane_message


    # -----------------------------------------------------------------
    # Main chat entry point
    # -----------------------------------------------------------------

    async def ask(self, prompt, channel):

        if not prompt or not prompt.strip():
            return "👋 Yes? What can I help with?"

        scope = self.scope(channel)

        await self.db.ensure_context(scope)
        chat = await self.db.get_active(scope)

        history = self._load_history(chat[1]) if chat else []

        await self.dbg(
            channel,
            f"📥 new prompt ({len(prompt)} chars), "
            f"{len(history)} prior message(s) in context"
        )

        try:

            if await self.needs_search(prompt, channel):

                result = await self.vane_search(prompt, history, channel)

                if result and result.get("message"):

                    reply = await self.condense_for_discord(
                        prompt,
                        result["message"],
                        result.get("sources", []),
                        channel
                    )

                else:

                    await self.dbg(
                        channel,
                        "↪️ search unavailable/empty, falling back "
                        "to a normal reply"
                    )

                    reply = await self.llama_chat(
                        messages=(
                            [{"role": "system", "content": SYSTEM_PROMPT}]
                            + history
                            + [{"role": "user", "content": prompt}]
                        ),
                        label="fallback_reply"
                    )

            else:

                await self.dbg(channel, "💬 answering directly (no search)")

                reply = await self.llama_chat(
                    messages=(
                        [{"role": "system", "content": SYSTEM_PROMPT}]
                        + history
                        + [{"role": "user", "content": prompt}]
                    ),
                    label="direct_reply"
                )

            if not reply:
                await self.dbg(channel, "❌ empty reply from model")
                return "⚠️ I couldn't reach the local model."

            new_history = history + [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": reply}
            ]

            title = None

            # Generate a title only early in the conversation.
            if len(history) < 6:
                title = await self.make_title(prompt)
                await self.dbg(channel, f"📝 title: {title}")

            await self.db.save_context(scope, new_history, title)

            await self.dbg(channel, f"✅ reply sent ({len(reply)} chars)")

            return reply

        except Exception as e:

            logger.error(f"ask() failed: {e}")
            return "⚠️ Something went wrong generating a reply."


bot = NautBot()


async def send_chunks(channel, text):

    while text:
        await channel.send(text[:1900])
        text = text[1900:]


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):

    if message.author.bot:
        return

    ctx = await bot.get_context(message)

    # Commands first
    if ctx.valid:
        await bot.invoke(ctx)
        return

    # DM = normal chat
    if isinstance(message.channel, discord.DMChannel):

        async with message.channel.typing():
            reply = await bot.ask(message.content, message.channel)
            await send_chunks(message.channel, reply)

        return

    # Server mention
    if bot.user in message.mentions:

        prompt = message.clean_content.replace(
            f"@{bot.user.display_name}", ""
        ).strip()

        async with message.channel.typing():
            reply = await bot.ask(prompt, message.channel)
            await send_chunks(message.channel, reply)


@bot.command()
async def clear(ctx):

    await bot.db.new_context(bot.scope(ctx.channel), "Fresh chat")
    await ctx.reply("🧹 Started a new conversation")


@bot.command(name="debug")
async def debug_cmd(ctx):

    scope = bot.scope(ctx.channel)
    new_state = bot.toggle_debug(scope)

    if new_state:
        await ctx.reply(
            "🐛 Debug mode **ON** — pipeline steps will be sent "
            "as quote blocks while I work."
        )
    else:
        await ctx.reply("🐛 Debug mode **OFF**")


@bot.command()
async def context(ctx):

    chat = await bot.db.get_active(bot.scope(ctx.channel))

    used = 0

    if chat:
        history = bot._load_history(chat[1])
        full_text = "\n".join(m["content"] for m in history)
        used = await bot.count_tokens(full_text)

    percent = used / bot.context_limit * 100

    await ctx.reply(
        f"🧠 Context: {used}/{bot.context_limit} tokens "
        f"({percent:.2f}%)"
    )


@bot.command()
async def chats(ctx):

    scope = bot.scope(ctx.channel)
    rows = await bot.db.list_contexts(scope)

    if not rows:
        await ctx.reply("No saved chats.")
        return

    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    lines = [
        "📚 **Saved contexts**",
        "",
        "```\n",
        "# Summary                 Updated       Context",
        "------------------------------------------------"
    ]

    for i, row in enumerate(rows):

        cid, title, updated, _legacy_count, context_json = row

        date = datetime.datetime.fromtimestamp(updated).strftime(
            "%m/%d %H:%M"
        )

        history = bot._load_history(context_json)
        full_text = "\n".join(m["content"] for m in history)
        tokens = await bot.count_tokens(full_text)

        pct = tokens / bot.context_limit * 100

        lines.append(
            f"{i+1} {title[:22]:22} {date:12} {pct:5.1f}%"
        )

    lines.append("```\nReact to load a context.")

    msg = await ctx.reply("\n".join(lines))

    for e in emojis[:len(rows)]:
        await msg.add_reaction(e)

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in emojis

    try:
        reaction, user = await bot.wait_for(
            "reaction_add", timeout=60, check=check
        )

        index = emojis.index(str(reaction.emoji))
        await bot.db.activate_context(scope, rows[index][0])
        await ctx.send("✅ Loaded conversation")

    except asyncio.TimeoutError:
        pass


@bot.command(name="search")
async def search_cmd(ctx, *, query):

    async with ctx.channel.typing():

        history = []
        chat = await bot.db.get_active(bot.scope(ctx.channel))
        if chat:
            history = bot._load_history(chat[1])

        result = await bot.vane_search(query, history, ctx.channel)

        if not result or not result.get("message"):
            await ctx.reply("No results (or Vane is unavailable).")
            return

        reply = await bot.condense_for_discord(
            query,
            result["message"],
            result.get("sources", []),
            ctx.channel
        )

    await send_chunks(ctx.channel, reply)


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
!debug

Server:
Mention me to chat
"""
    )


if __name__ == "__main__":

    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN")

    bot.run(token)