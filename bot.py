import os
import re
import logging
import collections
from typing import Deque, Dict, List

import discord
from discord.ext import commands
from openai import OpenAI

# Load environment variables from the environment (e.g., GitHub/hosting secrets)

# ======== Configuration ========
BOT_NAME = "jimmy"  # Trigger word (case-insensitive)
MEMORY_TURNS = 10   # Number of past user turns the bot has replied to (each turn contains user+assistant messages)
PERSONALITY = (
    "You are Jimmy, a human discord server member. "
    "keep your messages short, like a real human would, avoid emojis"
)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # Change if needed

# ======== Logging ========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jimmy")

# ======== Clients ========
if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable.")

os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
ai = OpenAI()

intents = discord.Intents.default()
intents.message_content = True  # must be enabled in the bot portal
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ======== Memory (per channel) ========
# Stores only interactions the bot responded to: a deque of turns:
# turn = {"user": "<original user message>", "assistant": "<bot reply>"}
ChannelMemory = Dict[int, Deque[Dict[str, str]]]
memory: ChannelMemory = {}

def build_history(channel_id: int) -> List[Dict[str, str]]:
    """
    Convert stored turns into an OpenAI chat history.
    Only includes messages the bot actually replied to, up to MEMORY_TURNS turns.
    """
    turns = memory.get(channel_id, collections.deque(maxlen=MEMORY_TURNS))
    chat: List[Dict[str, str]] = []
    for t in turns:
        chat.append({"role": "user", "content": t["user"]})
        chat.append({"role": "assistant", "content": t["assistant"]})
    return chat

def strip_bot_name(text: str, name: str) -> str:
    """
    Remove standalone occurrences of the bot's name from the message,
    to reduce noise in the prompt. Preserves the rest of the content.
    """
    pattern = rf"\b{re.escape(name)}\b"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

async def generate_reply(channel_id: int, user_content: str) -> str:
    """
    Call the OpenAI chat completion with system personality + limited history.
    """
    messages = [{"role": "system", "content": PERSONALITY}]
    messages += build_history(channel_id)
    messages.append({"role": "user", "content": user_content})

    try:
        resp = ai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.exception("OpenAI error: %s", e)
        return "I ran into a hiccup thinking that through. Try again in a moment."

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Listening for messages that include '{BOT_NAME}'")

@bot.event
async def on_message(message: discord.Message):
    # Ignore the bot's own messages and other bots
    if message.author.bot:
        return

    content = message.content or ""
    # Trigger when the bot's name is mentioned as a word (case-insensitive)
    if re.search(rf"\b{re.escape(BOT_NAME)}\b", content, flags=re.IGNORECASE):
        channel_id = message.channel.id
        cleaned = strip_bot_name(content, BOT_NAME)

        # Fallback if user only typed the name
        if not cleaned:
            cleaned = "You were mentioned by name. give a greeting."

        # Build and send reply
        reply_text = await generate_reply(channel_id, cleaned)
        try:
            sent = await message.reply(reply_text, mention_author=False)
        except discord.Forbidden:
            # If reply permissions fail, try sending a plain message
            sent = await message.channel.send(reply_text)

        # Update channel-scoped memory with this turn
        turns = memory.setdefault(channel_id, collections.deque(maxlen=MEMORY_TURNS))
        turns.append({"user": content, "assistant": reply_text})

    # Ensure commands still work
    await bot.process_commands(message)

# Optional: a health check command
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")

def main():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()
