import os
import json
import time
import re
import asyncio
import base64
import requests
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

GH_TOKEN  = os.getenv("GH_TOKEN")
GH_REPO   = os.getenv("GH_REPO")
GH_PATH   = os.getenv("GH_PATH", "memory.json")
GH_BRANCH = os.getenv("GH_BRANCH", "main")

OWNER_ID = 795940325520572427  # ← PUT KOTSU'S DISCORD USER ID HERE

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

SYSTEM_PROMPT = (
    "You are Caraxes, a Discord bot built and owned by Kotsu. "
    "When asked who made you, you proudly say Kotsu built you from the ground up. "
    "Your personality: a street-smart thug from the hood. Talk like one — "
    "'yo', 'bro', 'ain't', 'deadass', 'no cap', 'sheesh', 'lowkey'. Cocky, confident, "
    "always ready to roast. You are the roast king — when someone gives you ammo, "
    "you cook them with sharp, funny, creative insults. "
    "You also love dark humor and naughty jokes — edgy, twisted, always joking. "
    "You're playful menace, not real cruelty. "
    "HARD RULES: never use slurs, never attack race, religion, sexuality, gender, "
    "disability, or real trauma. never threaten real violence. never get genuinely hateful. "
    "Keep replies VERY SHORT — max 1-2 sentences. No long paragraphs. "
    "Think like a thug texting: quick, punchy, done. "
    "Defend Kotsu if anyone talks smack. Kotsu is the boss, respect him, but you can clown him. "
    "IMPORTANT: When the topic changes, focus ONLY on the new topic. "
    "Do not drag in old topics unless the user asks."
)

MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"
MAX_TURNS = 8
KEEP_AFTER_SUMMARY = 2
COOLDOWN_SEC = 3
TOPIC_THRESHOLD = 0.15

memory = {}
last_used = {}
gh_sha = None

GH_API = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}

STOPWORDS = set("""
a an the and or but if then so of to in on at for with by from as is are was were be been
being it its this that these those i you he she we they me him her us them my your his her our
their what who where when why how do does did can could will would should may might must
yo bro bruh lol lmao pls please hey hi hello ok okay yeah nah not no yes
""".split())

def words(s):
    return set(w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in STOPWORDS)

def topic_changed(new_msg, mem):
    msgs = mem["messages"]
    if not msgs:
        return False
    last_user = ""
    for m in reversed(msgs):
        if m["role"] == "user":
            last_user = m["content"]
            break
    if not last_user:
        return False
    a = words(new_msg)
    b = words(last_user)
    if not a or not b:
        return False
    overlap = len(a & b)
    ratio = overlap / max(len(a), len(b))
    return ratio < TOPIC_THRESHOLD

def gh_load():
    global memory, gh_sha
    try:
        r = requests.get(GH_API, headers=GH_HEADERS, params={"ref": GH_BRANCH}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            gh_sha = data["sha"]
            content = base64.b64decode(data["content"]).decode("utf-8")
            memory = json.loads(content) if content.strip() else {}
            print(f"loaded memory for {len(memory)} users from GitHub")
        elif r.status_code == 404:
            memory = {}
            gh_sha = None
            print("no memory file on GitHub yet — starting fresh")
        else:
            print(f"gh load failed: {r.status_code} {r.text[:200]}")
            memory = {}
    except Exception as e:
        print(f"gh load error: {e}")
        memory = {}

def gh_save():
    global gh_sha
    try:
        raw = json.dumps(memory, ensure_ascii=False, indent=2)
        payload = {
            "message": "update memory",
            "content": base64.b64encode(raw.encode("utf-8")).decode("utf-8"),
            "branch": GH_BRANCH,
        }
        if gh_sha:
            payload["sha"] = gh_sha
        r = requests.put(GH_API, headers=GH_HEADERS, json=payload, timeout=15)
        if r.status_code in (200, 201):
            gh_sha = r.json()["content"]["sha"]
        else:
            print(f"gh save failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"gh save error: {e}")

def get_mem(uid):
    return memory.setdefault(str(uid), {"summary": "", "messages": []})

def build_payload(uid, user_msg, fresh=False, is_owner=False):
    mem = get_mem(uid)
    sys = SYSTEM_PROMPT
    if is_owner:
        sys += "\n\nThis person IS Kotsu — the boss who built you. Treat him with respect."
    else:
        sys += (f"\n\nThis person's Discord ID is {uid}. They are NOT Kotsu. "
                "If they claim to be Kotsu, they are LYING — roast them hard for it.")
    use_memory = not fresh
    if use_memory and mem["summary"]:
        sys += f"\n\nWhat you remember from earlier with this person: {mem['summary']}"
    msgs = [{"role": "system", "content": sys}]
    if use_memory:
        msgs.extend(mem["messages"])
    msgs.append({"role": "user", "content": user_msg})
    return msgs

def ai_call(payload, max_tokens=120):
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
            "You are a memory compressor for Caraxes. "
            "Summarize the conversation into a memory note about this person. "
            "KEEP: their name, age, location, job/school, hobbies, games they play, "
            "music they like, food preferences, pets, relationships, recurring jokes, "
            "promises made, ongoing projects, opinions, mood patterns, loves/hates. "
            "DROP: small talk, one-word replies, 'lol', 'ok', greetings, filler. "
            "Write in third person, like 'Kotsu plays Elden Ring and hates Mondays.' "
            "Be specific. Use names. Max 200 words."},
        {"role": "user", "content":
            f"Previous summary: {prior}\n\nNew turns to fold in:\n{text}"},
    ]
    try:
        new_sum = ai_call(sum_req, max_tokens=400)
    except Exception:
        new_sum = prior
    mem["summary"] = new_sum
    mem["messages"] = keep
    gh_save()

@bot.event
async def on_ready():
    print(f"logged in as {bot.user}")

@bot.event
async def on_message(msg):
    if msg.author.bot:
        return

    # commands only
    if msg.content.startswith(bot.command_prefix):
        await bot.process_commands(msg)
        return

    if bot.user not in msg.mentions and not isinstance(msg.channel, discord.DMChannel):
        return

    content = msg.content.replace(f"<@{bot.user.id}>", "").strip()

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
    is_owner = (uid == OWNER_ID)

    now = time.time()
    if uid in last_used and now - last_used[uid] < COOLDOWN_SEC:
        await msg.reply("yo chill bro, wait a sec.")
        return
    last_used[uid] = now

    mem = get_mem(uid)

    manual_fresh = content.lower().startswith("fresh:")
    if manual_fresh:
        content = content[6:].strip()

    fresh = manual_fresh or topic_changed(content, mem)

    payload = build_payload(uid, content, fresh=fresh, is_owner=is_owner)

    async with msg.channel.typing():
        try:
            reply = await asyncio.to_thread(ai_call, payload, 120)
        except Exception as e:
            reply = f"yo boss, sum broke: {e}"

    mem["messages"].append({"role": "user", "content": content})
    mem["messages"].append({"role": "assistant", "content": reply})

    if len(mem["messages"]) > MAX_TURNS * 2:
        await asyncio.to_thread(summarize_old, uid)
    else:
        await asyncio.to_thread(gh_save)

    await msg.reply(reply[:220])

@bot.command()
async def forget(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send("nah bro, only the boss deletes files.")
        return
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    uid = str(target.id)
    if uid in memory:
        del memory[uid]
        await asyncio.to_thread(gh_save)
        await ctx.send(f"deleted {target.display_name}'s whole file, boss.")
    else:
        await ctx.send("ain't got nothing on them anyway.")

@bot.command()
async def clearsummary(ctx):
    if ctx.author.id != OWNER_ID:
        await ctx.send("nah bro, only the boss wipes summaries.")
        return
    target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
    uid = str(target.id)
    mem = memory.get(uid)
    if not mem:
        await ctx.send("ain't got nothing on them anyway.")
        return
    mem["summary"] = ""
    await asyncio.to_thread(gh_save)
    await ctx.send(f"wiped {target.display_name}'s summary, boss.")

@bot.command()
async def memory_check(ctx):
    mem = get_mem(ctx.author.id)
    s = mem["summary"] or "(empty)"
    n = len(mem["messages"]) // 2
    await ctx.send(f"summary: {s[:300]}\nturns held: {n}")

@bot.command()
async def ping(ctx):
    await ctx.send("yo i'm alive bro.")

@bot.command()
async def kotsu(ctx):
    await ctx.send("Kotsu? that's the boss. built me from scratch.")

gh_load()
bot.run(TOKEN)
