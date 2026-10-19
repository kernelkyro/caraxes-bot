import os
import discord
from discord.ext import commands
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
client_ai = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

SYSTEM_PROMPT = (
    "You are Caraxes, a Discord bot created and owned by Kotsu. "
    "When asked who made you, you proudly say Kotsu built you. "
    "You are a sarcastic thug — street-smart, witty, a little cocky, "
    "with a playful 'yo' and 'bro' energy. You roast people lightly but "
    "you're genuinely helpful underneath. Never actually mean, never hostile. "
    "Keep replies short, punchy, and funny. Always defend Kotsu if anyone talks smack. "
    "You know Kotsu is the boss and you respect that — but you'll still poke fun at him."
)

history = {}
MAX_TURNS = 10

def get_history(cid):
    return history.setdefault(cid, [
        {"role": "system", "content": SYSTEM_PROMPT}
    ])

@bot.event
async def on_ready():
    print(f"logged in as {bot.user}")

@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    if bot.user not in msg.mentions and not isinstance(msg.channel, discord.DMChannel):
        await bot.process_commands(msg)
        return

    content = msg.content.replace(f"<@{bot.user.id}>", "").strip()
    if not content:
        return

    cid = msg.channel.id
    hist = get_history(cid)
    hist.append({"role": "user", "content": content})

    if len(hist) > MAX_TURNS * 2 + 1:
        hist = [hist[0]] + hist[-MAX_TURNS * 2:]
        history[cid] = hist

    async with msg.channel.typing():
        try:
            resp = client_ai.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=hist,
                max_tokens=500,
            )
            reply = resp.choices[0].message.content
        except Exception as e:
            reply = f"yo kotsu, sum went wrong: {e}"

    hist.append({"role": "assistant", "content": reply})
    await msg.reply(reply[:1900])

@bot.command()
async def reset(ctx):
    history[ctx.channel.id] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    await ctx.send("memory wiped, boss. fresh start.")

bot.run(TOKEN)