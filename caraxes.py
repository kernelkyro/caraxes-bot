import os
import json
import time
import asyncio
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

MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"

MAX_TURNS = 8
KEEP_AFTER_SUMMARY = 2
COOLDOWN_SEC = 3

MEM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")

memory = {}
last_used = {}

def load_memory():
    global memory
    if os.path.exists(MEM_FILE):
        try:
            with open(MEM_FILE, "r", encoding="utf-8") as f:
                memory = json.load(f)
            print(f"loaded memory for {len(memory)} users")
        except Exception as e:
            print(f"memory load failed: {e}")
            memory = {}
    else:
        memory = {}

def save_memory():
    try:
        tmp = MEM_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MEM_FILE)
    except Exception as e:
        print(f"memory save failed: {e}")

def get_mem(uid):
    return memory.setdefault(str(uid), {"summary": "", "messages": []})

def build_payload(uid, user_msg):
    mem = get_mem(uid)
    sys = SYSTEM_PROMPT
    if mem["summary"]:
        sys += f"\n\nWhat you remember from earlier with this person: {mem['summary']}"
    msgs = [{"role": "system", "content": sys}]
    msgs.extend(mem["messages"])
    msgs.append({"role": "user", "content": user_msg})
    return msgs

def ai_call(payload, max_tokens=500):
    try:
        r = client_ai.chat.completions.create(
            model=MODEL, messages=payload, max_tokens=max_tokens
        )
        return r.choices[0].message.content
    except Exception:
        r = client_ai.chat.completions.create(
            model=FALLBACK_MODEL, messages=payload, max_tokens=max_tokens
        )
        return r.choices[0].message.content

def summarize_old(uid):
    mem = get_mem(uid)
    msgs = mem["messages"]
    if len(msgs) <= MAX_TURNS * 2:
        return
    old = msgs[:-KEEP_AFTER_SUMMARY * 2] if KEEP_AFTER_SUMMARY else msgs
    keep = msgs[-KEEP_AFTER_SUMMARY * 2:] if KEEP_AFTER_SUMMARY else []
    text = "\n".join(f"{m['role']}: {m['content']}" for m in old)
    prior = mem["summary"] or "none"
    sum_req = [
        {"role": "system", "content":
            "Summarize the conversation into a short memory note. "
            "Keep names, facts, preferences, jokes, promises. "
            "Third person. Max 120 words."},
        {"role": "user", "content":
            f"Previous summary: {prior}\n\nNew turns to fold in:\n{text}"},
    ]
    try:
        new_sum = ai_call(sum_req, max_tokens=300)
    except Exception:
        new_sum = prior
    mem["summary"] = new_sum
    mem["messages"] = keep
    save_memory()

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

    # reply context
    if msg.reference and msg.reference.message_id:
        try:
            ref = await msg.channel.fetch_message(msg.reference.message_id)
            if ref.content:
                content = f"(replying to: {ref.content[:300]}) {content}"
        except Exception:
            pass

    if not content:
        return

    uid = msg.author.id
    now = time.time()
    if uid in last_used and now - last_used[uid] < COOLDOWN_SEC:
        await msg.reply("yo chill bro, wait a sec.")
        return
    last_used[uid] = now

    mem = get_mem(uid)
    payload = build_payload(uid, content)

    async with msg.channel.typing():
        try:
            reply = await asyncio.to_thread(ai_call, payload, 500)
        except Exception as e:
            reply = f"yo kotsu, sum went wrong: {e}"

    mem["messages"].append({"role": "user", "content": content})
    mem["messages"].append({"role": "assistant", "content": reply})
    save_memory()

    if len(mem["messages"]) > MAX_TURNS * 2:
        asyncio.create_task(asyncio.to_thread(summarize_old, uid))

    await msg.reply(reply[:1900])

@bot.command()
async def reset(ctx):
    memory[str(ctx.author.id)] = {"summary": "", "messages": []}
    save_memory()
    await ctx.send("yo, memory wiped boss. clean slate.")

@bot.command()
async def memory_check(ctx):
    mem = get_mem(ctx.author.id)
    s = mem["summary"] or "(empty)"
    n = len(mem["messages"]) // 2
    await ctx.send(f"summary: {s[:300]}\nturns held: {n}")

@bot.command()
async def forget(ctx):
    uid = str(ctx.author.id)
    if uid in memory:
        del memory[uid]
        save_memory()
        await ctx.send("deleted your whole file, boss. who are you again?")
    else:
        await ctx.send("ain't got nothing on you anyway.")

@bot.command()
async def ping(ctx):
    await ctx.send("yo i'm alive bro. Caraxes in the building.")

@bot.command()
async def kotsu(ctx):
    await ctx.send("Kotsu? that's the boss. built me from scratch. respect the name.")

load_memory()
bot.run(TOKEN)
