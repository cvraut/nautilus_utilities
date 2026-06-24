#! /home/craut/miniconda3/envs/discbot/bin/python
import discord
from discord.ext import commands

import aiohttp
import asyncio
import logging
import os
import json
import datetime
import re

from urllib.parse import urlparse

import trafilatura

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

        # --- search/RAG tuning knobs ---

        # how many SearXNG results to even consider fetching
        self.search_results_to_try = 10

        # how many *successfully extracted* pages to actually use
        self.search_results_to_use = 7

        # how many raw results to request from SearXNG PER QUERY,
        # before rag_search's cross-query dedup and the
        # search_results_to_try cap are applied. Since make_search_
        # queries can produce up to 2 rewritten queries and results
        # get deduplicated by URL afterward, this needs to be at
        # least as large as search_results_to_try -- otherwise this
        # was silently throttling the candidate pool *before*
        # search_results_to_try ever got a chance to matter.
        self.searx_results_per_query = self.search_results_to_try

        # per-page character cap (post-extraction, not raw HTML)
        self.per_page_char_cap = 3000

        # total character budget across ALL pages combined,
        # so one big page can't crowd out the others
        self.total_web_context_char_cap = 6000

        # if trafilatura extracts less than this many characters,
        # treat it as a JS-rendered / extraction-failed page and skip it
        self.min_extracted_chars = 200

        # --- debug mode ---
        # per-scope toggle (DMs and each guild channel track this
        # independently). In-memory only: resets on bot restart,
        # since this is meant as a live "watch it work" session
        # tool, not a persisted setting.
        self.debug_scopes = {}

        # --- consensus checking ---

        # minimum number of sources that must agree on a value
        # before we present it as verified rather than flagging
        # uncertainty to the synthesis model
        self.consensus_min_sources = 2

        # numeric tolerance for "agreement" -- two pages saying
        # 68 and 69 should count as agreeing, 68 vs 53 should not.
        # this is a relative tolerance, not absolute, so it scales
        # sensibly across small and large numbers (temps vs prices)
        self.consensus_numeric_tolerance_pct = 0.05

        # known repeat offenders, seeded from observed failures.
        # Skipped before fetching at all -- saves the request.
        self.js_heavy_domains = {
            "weather.com",
            "www.accuweather.com",
            "accuweather.com",
            "x.com",
            "twitter.com",
            "www.instagram.com",
            "instagram.com",
        }

        # domains auto-added at runtime once we see them fail the
        # post-fetch heuristic twice. In-memory only (clears on
        # restart) -- intentionally not persisted, since sites do
        # change their rendering approach over time and we don't
        # want a permanent blacklist drifting out of date silently.
        self.js_heavy_strike_count = {}
        self.js_heavy_strike_threshold = 2


    def is_debug(self, scope):
        return self.debug_scopes.get(scope, False)


    def toggle_debug(self, scope):
        new_state = not self.is_debug(scope)
        self.debug_scopes[scope] = new_state
        return new_state


    async def dbg(self, channel, text):
        """
        Sends a debug breadcrumb as a Discord quote block, but only
        if debug mode is on for this channel's scope. No-ops
        otherwise, so call sites don't need to check is_debug
        themselves. Also no-ops safely if channel is None.
        """

        if channel is None:
            return

        if not self.is_debug(self.scope(channel)):
            return

        # quote-block every line, and chunk long text so Discord's
        # 2000 char limit doesn't truncate or error out
        quoted = "\n".join(
            f"> {line}" for line in text.splitlines()
        ) or "> (empty)"

        for i in range(0, len(quoted), 1900):
            try:
                await channel.send(quoted[i:i + 1900])
            except Exception as e:
                logger.warning(f"Debug send failed: {e}")


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


    async def _ollama_generate(
        self,
        prompt,
        num_predict=10,
        temperature=0,
        timeout=20
    ):
        """
        Small helper for cheap, single-shot, non-chat Ollama calls
        (classifiers, query rewriting, titles). Always stream=False.
        Returns "" on any failure rather than raising, since these
        calls should never break the main chat flow.
        """

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": num_predict,
                "temperature": temperature
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.ollama}/api/generate",
                    json=payload,
                    timeout=timeout
                ) as r:
                    data = await r.json()
                    return data.get("response", "").strip()

        except Exception as e:
            logger.warning(f"Ollama helper call failed: {e}")
            return ""


    async def make_title(self, text):
        response = await self._ollama_generate(
            prompt=(
                "Create a short chat title.\n"
                "Maximum 5 words.\n"
                "Only output the title.\n\n"
                "Message:\n" + text
            ),
            num_predict=10,
            temperature=0
        )

        return response or "New chat"


    async def needs_search(self, prompt, channel, history_hint=""):
        """
        Replaces the old keyword-substring check. Asks the model
        directly whether this message needs a live web search,
        instead of matching against a fixed word list.

        Fails closed (returns False) on any error/ambiguous output,
        since a missed search is much cheaper than a needless one.
        """

        classify_prompt = (
            "You are a binary classifier for a Discord chat bot.\n"
            "Decide if answering the user's message requires a "
            "live web search (current events, prices, scores, "
            "weather, recent releases, facts that change over "
            "time, or anything you would not reliably know without "
            "looking it up).\n"
            "Do NOT search for general knowledge, definitions, "
            "math, code help, opinions, or casual conversation.\n\n"
            "Answer with exactly one word: YES or NO.\n\n"
            f"Message: {prompt}\n"
            "Answer:"
        )

        response = await self._ollama_generate(
            prompt=classify_prompt,
            num_predict=3,
            temperature=0,
            timeout=15
        )

        decision = response.strip().upper().startswith("YES")

        await self.dbg(
            channel,
            f"🔍 needs_search? raw='{response.strip()}' -> {decision}"
        )

        return decision


    async def make_search_queries(self, prompt, channel):
        """
        Problem #1 fix: don't just hand the raw Discord message to
        SearXNG. Ask the model to rewrite it into 1-2 concise,
        keyword-style search queries.

        Falls back to the raw prompt if rewriting fails or returns
        garbage, so search is never worse than the old behavior.
        """

        rewrite_prompt = (
            "Rewrite the user's message into 1 or 2 short, "
            "keyword-style web search queries that would find the "
            "information needed to answer it. Strip filler words, "
            "remove conversational phrasing, keep names/dates/"
            "entities exact.\n\n"
            "Output ONLY the queries, one per line. No numbering, "
            "no explanation, no quotes.\n\n"
            f"Message: {prompt}\n"
            "Queries:"
        )

        response = await self._ollama_generate(
            prompt=rewrite_prompt,
            num_predict=40,
            temperature=0,
            timeout=15
        )

        queries = [
            line.strip("-• ").strip()
            for line in response.splitlines()
            if line.strip()
        ]

        # guard against empty / degenerate rewrites
        queries = [q for q in queries if len(q) >= 2][:2]

        if not queries:
            await self.dbg(
                channel,
                f"✏️ query rewrite failed/empty, falling back to "
                f"raw prompt as query"
            )
            return [prompt]

        await self.dbg(
            channel,
            "✏️ rewritten queries:\n"
            + "\n".join(f"  - {q}" for q in queries)
        )

        return queries


    async def extract_claim(self, query, page_text, page_url, channel):
        """
        Consensus-check step 1: ask a small model to pull a single
        structured claim out of one page's extracted text, instead
        of handing raw text straight to the final synthesis model.

        Returns a dict like:
          {"value": "68", "is_current": true, "is_relevant": true,
           "url": "..."}
        or None if the page doesn't actually contain a usable
        answer.

        Two independent checks guard against the two failure modes
        seen in practice:
          - is_current: catches forecast/historical values being
            mistaken for live data (the weather "Tonight Low: 53°F"
            bug)
          - is_relevant: catches pages that are topically adjacent
            but answer a DIFFERENT question than the one asked --
            e.g. an old "Ronaldo's record across 6 World Cups"
            article surfacing for "what was the most recent
            completed World Cup match" and being treated as if it
            answered that question
        """

        extract_prompt = (
            "You are extracting ONE specific factual value from a "
            "single web page to answer a question. Be strict and "
            "literal -- only report a value if it is explicitly "
            "present in the text below AND the page is actually "
            "about the specific thing being asked (not just a "
            "related/historical topic).\n\n"
            f"Question: {query}\n\n"
            "Page text:\n"
            f"{page_text[:2500]}\n\n"
            "Respond in EXACTLY this format, nothing else:\n"
            "VALUE: <the number or short answer, or NONE if not "
            "found>\n"
            "IS_CURRENT: <YES if this is explicitly a live/current/"
            "now reading, NO if it is a forecast, outlook, "
            "historical, or unclear, NONE if no value found>\n"
            "IS_RELEVANT: <YES if this page is specifically about "
            "the exact event/match/thing asked about, NO if it is "
            "about a different event, an older instance of a "
            "recurring event, a tangential record/stat, or general "
            "background rather than the specific thing asked, "
            "NONE if no value found>\n"
        )

        response = await self._ollama_generate(
            prompt=extract_prompt,
            num_predict=50,
            temperature=0,
            timeout=20
        )

        value = None
        is_current = False
        is_relevant = False

        for line in response.splitlines():

            line = line.strip()

            if line.upper().startswith("VALUE:"):
                v = line.split(":", 1)[1].strip()
                if v and v.upper() != "NONE":
                    value = v

            elif line.upper().startswith("IS_CURRENT:"):
                flag = line.split(":", 1)[1].strip().upper()
                is_current = flag.startswith("YES")

            elif line.upper().startswith("IS_RELEVANT:"):
                flag = line.split(":", 1)[1].strip().upper()
                is_relevant = flag.startswith("YES")

        if not value:
            await self.dbg(
                channel,
                f"🔎 claim extract: no value found on {page_url}"
            )
            return None

        if not is_relevant:
            await self.dbg(
                channel,
                f"🔎 claim extract: {page_url} -> value='{value}' "
                f"but REJECTED as not relevant to the specific "
                f"question asked (likely a different event or "
                f"tangential topic)"
            )
            return None

        await self.dbg(
            channel,
            f"🔎 claim extract: {page_url} -> "
            f"value='{value}' is_current={is_current} "
            f"is_relevant={is_relevant}"
        )

        return {
            "value": value,
            "is_current": is_current,
            "url": page_url
        }


    def values_agree(self, a, b):
        """
        Compares two extracted claim values for agreement.
        Tries numeric comparison with relative tolerance first
        (handles "68", "68F", "68°F" etc by stripping non-numeric
        chars); falls back to case-insensitive exact string match
        for non-numeric claims (e.g. sports scores like "2-1").
        """

        def to_number(s):
            cleaned = re.sub(r"[^0-9.\-]", "", s)
            try:
                return float(cleaned)
            except ValueError:
                return None

        na, nb = to_number(a), to_number(b)

        if na is not None and nb is not None:
            if na == 0 and nb == 0:
                return True
            denom = max(abs(na), abs(nb), 1e-9)
            return abs(na - nb) / denom <= self.consensus_numeric_tolerance_pct

        return a.strip().lower() == b.strip().lower()


    async def check_consensus(self, query, pages, channel):
        """
        Consensus-check step 2: extract a claim from each page,
        group agreeing values, and decide whether we have enough
        independent agreement to call the answer verified.

        Returns:
          {
            "verified": bool,
            "value": <agreed value, or None>,
            "supporting_urls": [...],
            "all_claims": [...]   # for transparency/debug, includes
                                   # disagreeing and rejected claims
          }
        """

        claim_tasks = [
            self.extract_claim(query, p["text"], p["url"], channel)
            for p in pages
        ]

        claims = await asyncio.gather(*claim_tasks)

        # only claims explicitly marked as current/live count
        # toward consensus -- this is the direct fix for the
        # "Tonight Low: 53°F" mistake, where the value existed
        # but wasn't actually a current reading
        usable_claims = [c for c in claims if c and c["is_current"]]

        rejected = [c for c in claims if c and not c["is_current"]]

        if rejected:
            await self.dbg(
                channel,
                f"⚠️ rejected {len(rejected)} non-current claim(s) "
                f"(forecast/unclear, not live data):\n"
                + "\n".join(
                    f"  - {c['url']}: '{c['value']}'"
                    for c in rejected
                )
            )

        if not usable_claims:
            await self.dbg(
                channel,
                "🚫 no usable current-data claims extracted from "
                "any source"
            )
            return {
                "verified": False,
                "value": None,
                "supporting_urls": [],
                "all_claims": claims
            }

        # group claims that agree with each other
        groups = []  # list of {"values": [...], "claims": [...]}

        for c in usable_claims:

            placed = False

            for g in groups:
                if self.values_agree(g["values"][0], c["value"]):
                    g["values"].append(c["value"])
                    g["claims"].append(c)
                    placed = True
                    break

            if not placed:
                groups.append({
                    "values": [c["value"]],
                    "claims": [c]
                })

        groups.sort(key=lambda g: len(g["claims"]), reverse=True)

        best = groups[0]

        await self.dbg(
            channel,
            f"🤝 consensus groups: "
            + " | ".join(
                f"[{g['values'][0]}: {len(g['claims'])} source(s)]"
                for g in groups
            )
        )

        if len(best["claims"]) >= self.consensus_min_sources:

            await self.dbg(
                channel,
                f"✅ verified: '{best['values'][0]}' agreed by "
                f"{len(best['claims'])} source(s)"
            )

            return {
                "verified": True,
                "value": best["values"][0],
                "supporting_urls": [
                    c["url"] for c in best["claims"]
                ],
                "all_claims": claims
            }

        await self.dbg(
            channel,
            f"⚠️ not verified: best agreement was only "
            f"{len(best['claims'])} source(s), need "
            f"{self.consensus_min_sources}"
        )

        return {
            "verified": False,
            "value": None,
            "supporting_urls": [],
            "all_claims": claims
        }


    def get_domain(self, url):
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""


    def is_known_js_heavy(self, url):
        return self.get_domain(url) in self.js_heavy_domains


    def record_js_heavy_strike(self, url):
        """
        Called when a page passes the pre-fetch heuristic check
        but still extracts to near-nothing. After enough strikes,
        the domain gets auto-added to the denylist so future
        requests skip the fetch entirely.
        """

        domain = self.get_domain(url)

        if not domain:
            return

        count = self.js_heavy_strike_count.get(domain, 0) + 1
        self.js_heavy_strike_count[domain] = count

        if count >= self.js_heavy_strike_threshold:
            self.js_heavy_domains.add(domain)
            logger.info(
                f"Auto-added {domain} to JS-heavy denylist "
                f"after {count} strikes"
            )


    def looks_js_heavy(self, html):
        """
        Cheap pre-extraction heuristic to spot client-rendered
        (SPA) pages BEFORE spending time on trafilatura. Looks at:

          - known SPA root markers (id="root", id="__next", etc.)
          - ratio of visible text to total HTML size
          - ratio of <script> tag content to total HTML size

        This is intentionally crude (regex, not a DOM parser) --
        it just needs to catch the obvious cases cheaply. Genuinely
        ambiguous pages fall through to the real extraction step
        and get caught by min_extracted_chars instead.
        """

        if not html:
            return True

        html_len = len(html)

        if html_len < 500:
            # essentially empty response
            return True

        # SPA root container markers
        spa_markers = (
            'id="root"', "id='root'",
            'id="__next"', "id='__next'",
            'id="app"', "id='app'",
            "ng-app", "data-reactroot",
        )

        if any(marker in html for marker in spa_markers):
            # don't bail immediately -- some sites use these IDs
            # but still server-render real content into them. Use
            # it as a strong signal, combined with low text ratio.
            spa_marker_hit = True
        else:
            spa_marker_hit = False

        # strip script/style blocks, then strip remaining tags,
        # to estimate how much actual visible text exists
        no_scripts = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE
        )

        visible_text = re.sub(r"<[^>]+>", " ", no_scripts)
        visible_text = re.sub(r"\s+", " ", visible_text).strip()

        text_ratio = len(visible_text) / html_len

        script_content_len = sum(
            len(m) for m in re.findall(
                r"<script[^>]*>(.*?)</script>",
                html,
                flags=re.DOTALL | re.IGNORECASE
            )
        )

        script_ratio = script_content_len / html_len

        # heuristic thresholds -- tuned loose on purpose, since a
        # false "skip" just falls back to the search snippet, while
        # a false "proceed" just costs one wasted trafilatura call
        if spa_marker_hit and text_ratio < 0.05:
            return True

        if script_ratio > 0.6 and text_ratio < 0.03:
            return True

        return False


    def extract_main_content(self, html, url):
        """
        Problem #2 fix: trafilatura instead of raw BeautifulSoup
        text dump. Trafilatura is built specifically to strip nav/
        ads/boilerplate and keep the article body.

        Returns "" if extraction yields too little text — this is
        the signal we use to detect JS-rendered pages that gave us
        an empty shell, so we skip them instead of feeding the
        model near-blank content.
        """

        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                favor_recall=True,
                include_comments=False,
                include_tables=False
            )

        except Exception as e:
            logger.warning(f"trafilatura failed for {url}: {e}")
            return ""

        if not extracted:
            return ""

        if len(extracted) < self.min_extracted_chars:
            # almost certainly a JS-shell page or a paywall stub
            logger.info(
                f"Skipping {url}: only "
                f"{len(extracted)} chars extracted "
                f"(likely needs JS rendering)"
            )
            return ""

        return extracted


    async def fetch_and_extract(self, session, url, channel):

        if self.is_known_js_heavy(url):
            await self.dbg(
                channel,
                f"⏭️ skipped (known JS-heavy domain): {url}"
            )
            return None

        try:
            async with session.get(
                url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            ) as r:
                html = await r.text()

        except Exception as e:
            logger.warning(f"Fetch failed {url}: {e}")
            await self.dbg(channel, f"❌ fetch failed: {url} ({e})")
            return None

        if self.looks_js_heavy(html):
            self.record_js_heavy_strike(url)
            await self.dbg(
                channel,
                f"⏭️ skipped (looks JS-rendered, low text ratio): "
                f"{url}"
            )
            return None

        text = self.extract_main_content(html, url)

        if not text:
            self.record_js_heavy_strike(url)
            await self.dbg(
                channel,
                f"⏭️ skipped (no usable content after extraction): "
                f"{url}"
            )
            return None

        await self.dbg(
            channel,
            f"✅ extracted {len(text)} chars: {url}"
        )

        return {
            "url": url,
            "text": text[: self.per_page_char_cap]
        }


    def empty_search_result(self):
        """
        Shared shape for rag_search's "nothing found" cases, so
        callers (ask(), the output verifier) always get the same
        dict structure regardless of which stage failed.
        """
        return {
            "text": "",
            "consensus": {
                "verified": False,
                "value": None,
                "supporting_urls": [],
                "all_claims": []
            }
        }


    async def rag_search(self, prompt, channel):
        """
        Full pipeline rewrite:
          1. rewrite prompt -> search queries  (fixes #1)
          2. SearXNG search per query
          3. fetch + trafilatura-extract top candidates,
             in parallel, skipping JS-empty pages   (fixes #2)
          4. cap per-page AND total chars            (fixes #4)
          5. return text with source URLs attached for citation
        """

        queries = await self.make_search_queries(prompt, channel)

        logger.info(f"Rewritten search queries: {queries}")

        # gather candidate results across all rewritten queries,
        # de-duplicating by URL
        seen_urls = set()
        candidates = []

        for q in queries:

            results = await self.searx_search(q)

            await self.dbg(
                channel,
                f"🌐 SearXNG '{q}' -> {len(results)} result(s)"
            )

            for r in results:

                url = r.get("url")

                if not url or url in seen_urls:
                    continue

                seen_urls.add(url)
                candidates.append(r)

        if not candidates:
            await self.dbg(channel, "🚫 no search candidates found")
            return self.empty_search_result()

        candidates = candidates[: self.search_results_to_try]

        await self.dbg(
            channel,
            f"📋 trying {len(candidates)} candidate page(s):\n"
            + "\n".join(f"  - {c['url']}" for c in candidates)
        )

        async with aiohttp.ClientSession(
            max_field_size=32768,
            max_line_size=32768
        ) as session:

            fetch_tasks = [
                self.fetch_and_extract(session, c["url"], channel)
                for c in candidates
            ]

            fetched = await asyncio.gather(*fetch_tasks)

        # drop failed/JS-empty pages, keep only what we need
        good_pages = [p for p in fetched if p][
            : self.search_results_to_use
        ]

        if not good_pages:
            await self.dbg(
                channel,
                "🚫 every candidate page failed extraction"
            )
            return self.empty_search_result()

        # Consensus check: extract a structured claim per page and
        # only mark a value "verified" if enough independent
        # sources agree AND explicitly label it as current/live
        # data (not a forecast/outlook value).
        consensus = await self.check_consensus(prompt, good_pages, channel)

        # enforce a combined budget so no single page (even under
        # its own per-page cap) crowds out the others
        chunks = []
        running_total = 0

        for page in good_pages:

            remaining = self.total_web_context_char_cap - running_total

            if remaining <= 0:
                await self.dbg(
                    channel,
                    f"✂️ total budget "
                    f"({self.total_web_context_char_cap} chars) "
                    f"reached, dropping remaining page(s)"
                )
                break

            snippet = page["text"][:remaining]

            chunks.append(f"Source: {page['url']}\n{snippet}")

            running_total += len(snippet)

        await self.dbg(
            channel,
            f"📦 final web context: {len(good_pages)} page(s) used, "
            f"{running_total}/{self.total_web_context_char_cap} "
            f"chars"
        )

        # prepend an explicit verification verdict ahead of the raw
        # page text, so the synthesis prompt's instructions (see
        # ask()) have something unambiguous to act on rather than
        # having to infer agreement from raw text itself.
        #
        # Uses [[INTERNAL_...]] markers rather than plain English
        # like "[VERIFIED ANSWER]" specifically so the model doesn't
        # mistake the instruction for user-facing content worth
        # echoing back -- the synthesis prompt also explicitly
        # tells it never to repeat these markers verbatim.
        if consensus["verified"]:

            verdict = (
                f"[[INTERNAL_VERIFIED_VALUE: {consensus['value']}]]\n"
                f"(confirmed by {len(consensus['supporting_urls'])} "
                f"independent sources: "
                f"{', '.join(consensus['supporting_urls'])})\n\n"
            )

        else:

            verdict = (
                "[[INTERNAL_NOT_VERIFIED]]\n"
                f"(no value confirmed by at least "
                f"{self.consensus_min_sources} independent "
                "current/live sources -- sources may disagree, be "
                "forecasts rather than current readings, be about "
                "a different event than asked, or be missing this "
                "data entirely)\n\n"
            )

        # Return both the text context (for the synthesis prompt)
        # AND the structured consensus result (for the output
        # verifier), so the verifier can check the reply against
        # the actual agreed-upon value directly, rather than having
        # to re-derive "is 69 somewhere in this 6000 char blob of
        # raw page text" via literal text matching.
        return {
            "text": verdict + "\n\n".join(chunks),
            "consensus": consensus
        }


    async def verify_reply_grounded(
        self,
        reply,
        web_context,
        consensus,
        channel
    ):
        """
        Output verification step: checks whether the specific
        factual claims in the model's reply are actually supported
        by the source material, rather than trusting that "I told
        it to cite sources" means it did so honestly.

        Two complementary checks:
          1. If consensus produced a verified value, check directly
             whether the reply states that value (in its own
             words). This is a much more reliable check than asking
             a small model to literal-match a number buried inside
             several thousand characters of differently-phrased raw
             page text, and it's the actual ground truth we already
             computed -- no reason to make the verifier re-derive it.
          2. Regardless of #1, also check the reply against the raw
             source text, for any additional claims it makes beyond
             the single verified value (names, context, etc).

        Returns True if grounded, False if not. Fails OPEN (treats
        as grounded) only when there was no web_context to check
        against in the first place. Fails CLOSED on any actual
        verification error.
        """

        if not web_context.strip():
            # no search was performed this turn -- nothing to
            # ground against, so this check doesn't apply
            return True

        # --- Fast, reliable path: check against the verified value
        # directly, when we have one ---
        if consensus and consensus.get("verified"):

            verified_value = consensus["value"]

            value_check_prompt = (
                f"A verified fact is: {verified_value}\n\n"
                f"Does the following REPLY state this value (in its "
                f"own words -- exact phrasing doesn't matter, the "
                f"number/fact just needs to match) as its main "
                f"answer, AND avoid stating any DIFFERENT "
                f"conflicting value for the same thing?\n\n"
                f"REPLY:\n{reply[:1500]}\n\n"
                "Respond with EXACTLY one word, nothing else: "
                "MATCH if the reply states this value and doesn't "
                "contradict it, or MISMATCH if the reply states a "
                "different/conflicting value or omits it entirely."
            )

            value_response = await self._ollama_generate(
                prompt=value_check_prompt,
                num_predict=12,
                temperature=0,
                timeout=20
            )

            value_verdict = value_response.strip().upper()
            value_matches = value_verdict.startswith("MATCH")

            await self.dbg(
                channel,
                f"🧪 verified-value check: expected="
                f"'{verified_value}' raw='{value_verdict}' -> "
                f"{'MATCH' if value_matches else 'MISMATCH'}"
            )

            if not value_matches:
                # no need to even run the general grounding check --
                # the reply already contradicts the one fact we
                # actually trust
                return False

            # The reply correctly states our verified value. Trust
            # this and skip the general literal-text-matching check
            # entirely -- that check is the less reliable signal
            # (a small model literal-matching a number inside
            # thousands of chars of differently-phrased raw text),
            # and we already have stronger evidence here: the value
            # itself was independently confirmed by 2+ sources
            # during the consensus step, and the reply states it.
            return True

        # --- General path: only reached when there's no verified
        # consensus value to check against directly (either
        # consensus wasn't verified, or no search/consensus ran at
        # all but web_context still has raw search text). Falls
        # back to literal-text-matching against raw source text. ---

        # Strip internal instruction markers before checking
        # grounding -- these are directives for the synthesis
        # model, not actual source content.
        source_text_only = re.sub(
            r"\[\[INTERNAL_[^\]]*\]\]",
            "",
            web_context
        )

        verify_prompt = (
            "You are a strict fact-checker. Below is a SOURCE TEXT "
            "and a REPLY that was supposed to be based on it.\n\n"
            "Check whether every specific factual claim in the "
            "REPLY (numbers, names, scores, dates, attributed "
            "events) is actually stated in the SOURCE TEXT. The "
            "reply does not need to use the same words, but the "
            "facts must genuinely appear in the source, not just "
            "be topically similar. General statements, hedges, "
            "offers to help further, and conversational filler "
            "are not factual claims and don't need to be checked.\n\n"
            "If the REPLY attaches a citation URL, also check that "
            "URL's own source block actually supports the claim "
            "next to it, not some other claim from a different "
            "source block.\n\n"
            f"SOURCE TEXT:\n{source_text_only[:5000]}\n\n"
            f"REPLY:\n{reply[:1500]}\n\n"
            "Respond with EXACTLY one word, nothing else: GROUNDED "
            "if every specific factual claim is genuinely supported "
            "by the source text, or UNGROUNDED if any claim is "
            "invented, unsupported, or mismatched to the wrong "
            "source."
        )

        response = await self._ollama_generate(
            prompt=verify_prompt,

            # NOTE: this needs enough headroom for the model to
            # cleanly emit "UNGROUNDED" (a longer/rarer token
            # sequence than "GROUNDED") plus any stray leading
            # token/whitespace some local models prepend despite
            # being told to answer with one word. A too-tight cap
            # here risks truncating a real verdict mid-word, which
            # then fails the startswith() check and gets
            # misread as UNGROUNDED regardless of the model's
            # actual judgment.
            num_predict=12,

            temperature=0,
            timeout=20
        )

        verdict = response.strip().upper()

        grounded = verdict.startswith("GROUNDED")

        await self.dbg(
            channel,
            f"🧪 output verification: raw='{verdict}' -> "
            f"{'GROUNDED' if grounded else 'UNGROUNDED'}"
        )

        return grounded


    async def generate_reply(
        self,
        prompt_text,
        context,
        web_context,
        channel,
        strict=False
    ):
        """
        Single Ollama generation call, factored out of ask() so it
        can be called twice (initial attempt + stricter retry)
        without duplicating the payload/request logic.
        """

        strict_addendum = ""

        if strict:
            strict_addendum = (
                "\n\nIMPORTANT: your previous attempt at this reply "
                "included a claim that could not be verified against "
                "the source text. This time, be extremely "
                "conservative: only state facts that are explicitly "
                "and literally present in the web results below. If "
                "you are not certain a specific detail (name, "
                "number, score) is in the source text, leave it out "
                "or say it's unconfirmed rather than guessing.\n"
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
If you use web search results, briefly cite the source URL -- but
ONLY cite a URL if that exact source actually supports the claim
you are attaching it to. Never attach a citation to a fact it
doesn't support.

The web results below may start with an internal marker line
starting with "[[INTERNAL_" -- these are instructions for you
only. NEVER repeat, quote, or mention these markers in your reply
to the user; they are not meant to be seen by them.

If you see [[INTERNAL_VERIFIED_VALUE: ...]], that value has
already been cross-checked against multiple independent live
sources for this exact question -- state it directly and
confidently, in your own words, without quoting the marker.

If you see [[INTERNAL_NOT_VERIFIED]], do NOT state a specific
current number/value as fact, even if one appears somewhere in the
source text below. Tell the user the current value couldn't be
confirmed and briefly summarize what the sources did say instead
(e.g. forecast ranges, conflicting reports, or that the sources
were about a different event/match than what was asked).
{strict_addendum}
Rules:
- Keep answers concise.
- Avoid huge paragraphs.
- Use emojis when helpful.
- Discord messages must be readable.

Use this information when relevant:

{web_context}

User:
""".format(
                web_context=web_context,
                strict_addendum=strict_addendum
            )
            + prompt_text,

            "context": context,

            "stream": False,

            "options": {

                "temperature": 0.7 if not strict else 0.2,

                # Discord friendly
                "num_predict": 700
            }
        }

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

        reply = re.sub(
            r"\[\[INTERNAL_[^\]]*\]\]",
            "",
            reply
        ).strip()

        new_context = data.get(
            "context",
            []
        )

        return reply, new_context


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

        web_context = ""
        consensus = None

        # Problem #3 fix: ask the model whether this needs a
        # search instead of matching a fixed keyword list.
        if await self.needs_search(prompt, channel):

            search_result = await self.rag_search(
                prompt,
                channel
            )

            consensus = search_result["consensus"]

            if search_result["text"]:

                web_context = """
Web search results (cite sources by URL when you use them):

""" + search_result["text"]

        # debug to make sure that it actually searched
        logger.info(
            f"Web context for prompt '{prompt}': {web_context[:500]}"
        )

        try:

            reply, new_context = await self.generate_reply(
                prompt,
                context,
                web_context,
                channel,
                strict=False
            )

            # Output verification: check the reply's claims
            # actually appear in the source text before trusting
            # it. Only meaningful when search was used this turn.
            # Passing the structured consensus result lets the
            # verifier check "does the reply state the verified
            # value" directly, instead of having to re-derive that
            # value itself from a few thousand chars of raw,
            # differently-worded page text.
            grounded = await self.verify_reply_grounded(
                reply,
                web_context,
                consensus,
                channel
            )

            if not grounded:

                await self.dbg(
                    channel,
                    "🔁 reply failed grounding check, retrying "
                    "with stricter instructions"
                )

                retry_reply, retry_context = await self.generate_reply(
                    prompt,
                    context,
                    web_context,
                    channel,
                    strict=True
                )

                retry_grounded = await self.verify_reply_grounded(
                    retry_reply,
                    web_context,
                    consensus,
                    channel
                )

                if retry_grounded:

                    reply, new_context = retry_reply, retry_context

                else:

                    await self.dbg(
                        channel,
                        "🚫 retry also failed grounding -- falling "
                        "back to a safe non-answer instead of "
                        "showing unverified content"
                    )

                    reply = (
                        "⚠️ I found some search results but couldn't "
                        "confirm a specific answer well enough to be "
                        "confident in it. Want me to share the raw "
                        "sources I found instead?"
                    )

                    new_context = retry_context


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
            )[: self.searx_results_per_query]


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
        await ctx.reply(
            "🐛 Debug mode **OFF**"
        )



@bot.command(name="jsdenylist")
async def js_denylist_cmd(ctx):

    domains = sorted(bot.js_heavy_domains)
    strikes = bot.js_heavy_strike_count

    lines = ["🚫 **JS-heavy domain denylist**", ""]

    for d in domains:
        s = strikes.get(d, "seed")
        lines.append(f"  - {d} ({s})")

    if not domains:
        lines.append("  (empty)")

    await ctx.reply("\n".join(lines))



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
!debug
!jsdenylist

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