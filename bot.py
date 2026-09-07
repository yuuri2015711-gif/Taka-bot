import os
import sqlite3
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands
from discord import app_commands

TOKEN = os.getenv("DISCORD_TOKEN")

PREFIX = "t"
DB_FILE = "taka_bot.db"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)

db = sqlite3.connect(DB_FILE)
db.row_factory = sqlite3.Row

db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    money INTEGER NOT NULL DEFAULT 1000,
    daily_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    salary INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS work_logs (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, work_date)
);

CREATE TABLE IF NOT EXISTS warnings (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS forbidden_words (
    guild_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS spam_settings (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    max_count INTEGER NOT NULL DEFAULT 5,
    seconds INTEGER NOT NULL DEFAULT 10
);

CREATE TABLE IF NOT EXISTS bot_admins (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS announcement_perms (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
""")

db.commit()

spam_messages = defaultdict(deque)


def ensure_user(user_id):
    db.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )
    db.commit()


def get_money(user_id):
    ensure_user(user_id)

    row = db.execute(
        "SELECT money FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    return row["money"]


def add_money(user_id, amount):
    ensure_user(user_id)

    db.execute(
        "UPDATE users SET money = money + ? WHERE user_id = ?",
        (amount, user_id)
    )

    db.commit()


def get_company(guild_id, owner_id):
    return db.execute(
        "SELECT * FROM companies WHERE guild_id = ? AND owner_id = ?",
        (guild_id, owner_id)
    ).fetchone()


def get_employee(guild_id, user_id):
    return db.execute(
        "SELECT * FROM employees WHERE guild_id = ? AND user_id = ?",

        (guild_id, user_id)
    ).fetchone()


def is_bot_admin(guild_id, user_id):
    return db.execute(
        "SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    ).fetchone() is not None


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print("Slash command sync error:", e)

    print(f"たかbot 起動完了: {bot.user}")
# =========================
# Part 2: お金・会社システム
# =========================

db.execute("""
CREATE TABLE IF NOT EXISTS work_status (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    clocked_in INTEGER NOT NULL DEFAULT 0,
    clocked_at INTEGER NOT NULL DEFAULT 0,
    last_work_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
)
""")
db.commit()


def get_work_status(guild_id, user_id):
    row = db.execute(
        "SELECT * FROM work_status WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    ).fetchone()

    if row is None:
        db.execute(
            """
            INSERT INTO work_status
            (guild_id, user_id, clocked_in, clocked_at, last_work_at)
            VALUES (?, ?, 0, 0, 0)
            """,
            (guild_id, user_id)
        )
        db.commit()

        row = db.execute(
            "SELECT * FROM work_status WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        ).fetchone()

    return row


# -------------------------
# 残高
# -------------------------

@bot.command(name="残高")
async def balance(ctx):
    money = get_money(ctx.author.id)

    await ctx.send(
        f"💰 {ctx.author.mention} の残高は **{money:,} coins** です。"
    )


# -------------------------
# お金確認
# -------------------------

@bot.command(name="お金確認")
@commands.has_permissions(administrator=True)
async def money_check(ctx, member: discord.Member):
    money = get_money(member.id)

    await ctx.send(
        f"💰 {member.mention} の残高は **{money:,} coins** です。"
    )


# -------------------------
# デイリー
# -------------------------

@bot.command(name="デイリー")
async def daily(ctx):
    ensure_user(ctx.author.id)

    row = db.execute(
        "SELECT daily_at FROM users WHERE user_id = ?",
        (ctx.author.id,)
    ).fetchone()

    now = int(time.time())
    cooldown = 60 * 60 * 24

    if now - row["daily_at"] < cooldown:
        remaining = cooldown - (now - row["daily_at"])
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60

        await ctx.send(
            f"⏳ まだ受け取れません。\n"
            f"あと **{hours}時間{minutes}分** です。"
        )
        return

    amount = 500

    add_money(ctx.author.id, amount)

    db.execute(
        "UPDATE users SET daily_at = ? WHERE user_id = ?",
        (now, ctx.author.id)
    )
    db.commit()

    await ctx.send(
        f"🎁 デイリーボーナスとして **{amount:,} coins** 獲得しました！"
    )


# -------------------------
# ランキング
# -------------------------

@bot.command(name="ランキング")
async def ranking(ctx):
    rows = db.execute(
        "SELECT user_id, money FROM users "
        "ORDER BY money DESC LIMIT 10"
    ).fetchall()

    if not rows:
        await ctx.send("📊 まだランキングデータがありません。")
        return

    text = "🏆 **所持金ランキング TOP10**\n\n"

    for i, row in enumerate(rows, start=1):
        user = bot.get_user(row["user_id"])

        if user:
            name = user.display_name
        else:
            name = f"ユーザー {row['user_id']}"

        text += f"**{i}位** {name}　💰 {row['money']:,} coins\n"

    await ctx.send(text)


# =========================
# 会社
# =========================

# 会社設立
@bot.command(name="会社設立")
async def create_company(ctx, salary: int, *, company_name: str):
    if salary <= 0:
        await ctx.send("❌ 給料は1以上にしてください。")
        return

    if len(company_name) > 50:
        await ctx.send("❌ 会社名は50文字以内にしてください。")
        return

    existing = get_company(ctx.guild.id, ctx.author.id)

    if existing:
        await ctx.send(
            f"❌ あなたはすでに **{existing['name']}** を経営しています。"
        )
        return

    db.execute(
        """
        INSERT INTO companies
        (guild_id, owner_id, name, salary)
        VALUES (?, ?, ?, ?)
        """,
        (
            ctx.guild.id,
            ctx.author.id,
            company_name,
            salary
        )
    )
    db.commit()

    await ctx.send(
        f"🏢 **会社設立完了！**\n"
        f"会社名：**{company_name}**\n"
        f"給料：**{salary:,} coins**\n"
        f"社長：{ctx.author.mention}"
    )


# 入社
@bot.command(name="入社")
async def join_company(ctx, owner: discord.Member):
    company = get_company(ctx.guild.id, owner.id)

    if not company:
        await ctx.send("❌ 指定されたユーザーは会社を経営していません。")
        return

    existing = get_employee(ctx.guild.id, ctx.author.id)

    if existing:
        await ctx.send("❌ あなたはすでに会社に所属しています。")
        return

    if ctx.author.id == owner.id:
        await ctx.send("❌ 自分の会社には入社できません。")
        return

    db.execute(
        """
        INSERT INTO employees
        (guild_id, user_id, company_id)
        VALUES (?, ?, ?)
        """,
        (
            ctx.guild.id,
            ctx.author.id,
            company["id"]
        )
    )
    db.commit()

    await ctx.send(
        f"✅ {ctx.author.mention} が **{company['name']}** に入社しました！"
    )


# 退社
@bot.command(name="退社")
async def leave_company(ctx):
    employee = get_employee(ctx.guild.id, ctx.author.id)

    if not employee:
        await ctx.send("❌ あなたは会社に所属していません。")
        return

    db.execute(
        """
        DELETE FROM employees
        WHERE guild_id = ? AND user_id = ?
        """,
        (ctx.guild.id, ctx.author.id)
    )

    db.execute(
        """
        DELETE FROM work_status
        WHERE guild_id = ? AND user_id = ?
        """,
        (ctx.guild.id, ctx.author.id)
    )

    db.commit()

    await ctx.send(
        f"👋 {ctx.author.mention} は会社を退社しました。"
    )


# 出勤
@bot.command(name="出勤")
async def clock_in(ctx):
    employee = get_employee(ctx.guild.id, ctx.author.id)

    if not employee:
        await ctx.send("❌ 会社に入社してから出勤してください。")
        return

    status = get_work_status(ctx.guild.id, ctx.author.id)

    if status["clocked_in"]:
        await ctx.send("❌ すでに出勤しています。")
        return

    now = int(time.time())

    db.execute(
        """
        UPDATE work_status
        SET clocked_in = 1,
            clocked_at = ?,
            last_work_at = 0
        WHERE guild_id = ? AND user_id = ?
        """,
        (now, ctx.guild.id, ctx.author.id)
    )
    db.commit()

    await ctx.send(
        f"🟢 {ctx.author.mention} が **出勤** しました！"
    )


# 働く
@bot.command(name="働く")
async def work(ctx):
    employee = get_employee(ctx.guild.id, ctx.author.id)

    if not employee:
        await ctx.send("❌ 会社に入社してください。")
        return

    status = get_work_status(ctx.guild.id, ctx.author.id)

    if not status["clocked_in"]:
        await ctx.send("❌ 先に `t出勤` してください。")
        return

    now = int(time.time())

    if now - status["last_work_at"] < 60:
        remaining = 60 - (now - status["last_work_at"])

        await ctx.send(
            f"⏳ 次に働けるまで **{remaining}秒** です。"
        )
        return

    company = db.execute(
        "SELECT * FROM companies WHERE id = ?",
        (employee["company_id"],)
    ).fetchone()

    if not company:
        await ctx.send("❌ 所属会社が見つかりません。")
        return

    salary = company["salary"]

    add_money(ctx.author.id, salary)

    db.execute(
        """
        UPDATE work_status
        SET last_work_at = ?
        WHERE guild_id = ? AND user_id = ?
        """,
        (now, ctx.guild.id, ctx.author.id)
    )

    today = time.strftime("%Y-%m-%d")

    db.execute(
        """
        INSERT INTO work_logs
        (guild_id, user_id, work_date, minutes)
        VALUES (?, ?, ?, 60)
        ON CONFLICT(guild_id, user_id, work_date)
        DO UPDATE SET minutes = minutes + 60
        """,
        (ctx.guild.id, ctx.author.id, today)
    )

    db.commit()

    await ctx.send(
        f"💼 仕事をしました！\n"
        f"💰 **+{salary:,} coins**"
    )


# 退勤
@bot.command(name="退勤")
async def clock_out(ctx):
    status = get_work_status(ctx.guild.id, ctx.author.id)

    if not status["clocked_in"]:
        await ctx.send("❌ 出勤していません。")
        return

    db.execute(
        """
        UPDATE work_status
        SET clocked_in = 0,
            clocked_at = 0,
            last_work_at = 0
        WHERE guild_id = ? AND user_id = ?
        """,
        (ctx.guild.id, ctx.author.id)
    )
    db.commit()

    await ctx.send(
        f"🔴 {ctx.author.mention} が **退勤** しました！"
    )
# =========================
# Part 3: タイムカード・会社撤去・警告・禁止ワード
# =========================

# タイムカード
@bot.command(name="タイムカード")
async def time_card(ctx):
    employee = get_employee(ctx.guild.id, ctx.author.id)

    if not employee:
        await ctx.send("❌ 会社に所属していません。")
        return

    company = db.execute(
        "SELECT * FROM companies WHERE id = ?",
        (employee["company_id"],)
    ).fetchone()

    today = time.strftime("%Y-%m-%d")

    row = db.execute(
        """
        SELECT minutes FROM work_logs
        WHERE guild_id = ? AND user_id = ? AND work_date = ?
        """,
        (ctx.guild.id, ctx.author.id, today)
    ).fetchone()

    minutes = row["minutes"] if row else 0

    status = get_work_status(ctx.guild.id, ctx.author.id)

    if status["clocked_in"]:
        state = "🟢 出勤中"
    else:
        state = "🔴 退勤中"

    await ctx.send(
        f"🕐 **タイムカード**\n"
        f"会社：**{company['name']}**\n"
        f"本日の勤務時間：**{minutes}分**\n"
        f"現在の状態：**{state}**"
    )


# 会社撤去
@bot.command(name="会社撤去")
async def remove_company(ctx):
    company = get_company(ctx.guild.id, ctx.author.id)

    if not company:
        await ctx.send("❌ あなたが経営している会社はありません。")
        return

    db.execute(
        "DELETE FROM employees WHERE company_id = ?",
        (company["id"],)
    )

    db.execute(
        "DELETE FROM companies WHERE id = ?",
        (company["id"],)
    )

    db.commit()

    await ctx.send(
        f"🏢 **{company['name']}** を撤去しました。"
    )


# =========================
# 警告システム
# =========================

@bot.command(name="警告")
@commands.has_permissions(moderate_members=True)
async def warn(ctx, member: discord.Member, count: int = 1, *, reason: str = "理由なし"):
    if member.bot:
        await ctx.send("❌ Botには警告できません。")
        return

    if count < 1:
        await ctx.send("❌ 件数は1以上にしてください。")
        return

    row = db.execute(
        """
        SELECT count FROM warnings
        WHERE guild_id = ? AND user_id = ?
        """,
        (ctx.guild.id, member.id)
    ).fetchone()

    current = row["count"] if row else 0
    new_count = current + count

    db.execute(
        """
        INSERT INTO warnings (guild_id, user_id, count)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET count = ?
        """,
        (
            ctx.guild.id,
            member.id,
            new_count,
            new_count
        )
    )

    db.commit()

    await ctx.send(
        f"⚠️ {member.mention} に **{count}件**の警告を付与しました。\n"
        f"現在の警告数：**{new_count}件**\n"
        f"理由：{reason}"
    )


# 警告リセット
@bot.command(name="警告リセット")
@commands.has_permissions(moderate_members=True)
async def reset_warn(ctx, member: discord.Member):
    db.execute(
        """
        INSERT INTO warnings (guild_id, user_id, count)
        VALUES (?, ?, 0)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET count = 0
        """,
        (ctx.guild.id, member.id)
    )

    db.commit()

    await ctx.send(
        f"♻️ {member.mention} の警告をリセットしました。"
    )


# 警告を減らす
@bot.command(name="警告を減らす")
@commands.has_permissions(moderate_members=True)
async def reduce_warn(
    ctx,
    member: discord.Member,
    count: int = 1
):
    if count < 1:
        await ctx.send("❌ 件数は1以上にしてください。")
        return

    row = db.execute(
        """
        SELECT count FROM warnings
        WHERE guild_id = ? AND user_id = ?
        """,
        (ctx.guild.id, member.id)
    ).fetchone()

    current = row["count"] if row else 0
    new_count = max(0, current - count)

    db.execute(
        """
        INSERT INTO warnings (guild_id, user_id, count)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET count = ?
        """,
        (
            ctx.guild.id,
            member.id,
            new_count,
            new_count
        )
    )

    db.commit()

    await ctx.send(
        f"➖ {member.mention} の警告を **{count}件**減らしました。\n"
        f"現在の警告数：**{new_count}件**"
    )


# =========================
# 禁止ワード
# =========================

@bot.command(name="禁止ワード")
@commands.has_permissions(manage_guild=True)
async def add_forbidden_word(ctx, *, word: str):
    word = word.strip()

    if not word:
        await ctx.send("❌ 禁止ワードを指定してください。")
        return

    if len(word) > 100:
        await ctx.send("❌ 禁止ワードは100文字以内にしてください。")
        return

    db.execute(
        """
        INSERT OR IGNORE INTO forbidden_words
        (guild_id, word)
        VALUES (?, ?)
        """,
        (ctx.guild.id, word.lower())
    )

    db.commit()

    await ctx.send(
        f"🚫 禁止ワード **{word}** を登録しました。"
    )


# 禁止ワード削除
@bot.command(name="禁止ワード削除")
@commands.has_permissions(manage_guild=True)
async def remove_forbidden_word(ctx, *, word: str):
    word = word.strip().lower()

    result = db.execute(
        """
        DELETE FROM forbidden_words
        WHERE guild_id = ? AND word = ?
        """,
        (ctx.guild.id, word)
    )

    db.commit()

    if result.rowcount == 0:
        await ctx.send("❌ その禁止ワードは登録されていません。")
        return

    await ctx.send(
        f"✅ 禁止ワード **{word}** を削除しました。"
    )


# =========================
# スパム保護設定
# =========================

@bot.command(name="スパム保護")
@commands.has_permissions(manage_guild=True)
async def spam_protection(
    ctx,
    status: str,
    max_count: int = 5,
    seconds: int = 10
):
    status = status.lower()

    if status not in ["有効", "無効"]:
        await ctx.send(
            "❌ `有効` または `無効` を指定してください。\n"
            "例：`tスパム保護 有効 5 10`"
        )
        return

    if max_count < 2 or seconds < 1:
        await ctx.send("❌ 件数は2以上、秒数は1以上にしてください。")
        return

    enabled = 1 if status == "有効" else 0

    db.execute(
        """
        INSERT INTO spam_settings
        (guild_id, enabled, max_count, seconds)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            enabled = ?,
            max_count = ?,
            seconds = ?
        """,
        (
            ctx.guild.id,
            enabled,
            max_count,
            seconds,
            enabled,
            max_count,
            seconds
        )
    )

    db.commit()

    await ctx.send(
        f"🛡️ スパム保護を **{status}** にしました。\n"
        f"設定：**{max_count}件 / {seconds}秒**"
    )
# =========================
# Part 4: 自動保護・コマンド一覧・スラッシュコマンド
# =========================

# -------------------------
# 禁止ワード・スパム自動検知
# -------------------------

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild is None:
        await bot.process_commands(message)
        return

    content = message.content.lower()

    # 禁止ワードチェック
    words = db.execute(
        """
        SELECT word FROM forbidden_words
        WHERE guild_id = ?
        """,
        (message.guild.id,)
    ).fetchall()

    for row in words:
        if row["word"] and row["word"].lower() in content:
            try:
                await message.delete()
                await message.channel.send(
                    f"🚫 {message.author.mention} "
                    f"禁止ワードが含まれているためメッセージを削除しました。",
                    delete_after=5
                )
            except discord.HTTPException:
                pass

            return

    # スパムチェック
    setting = db.execute(
        """
        SELECT * FROM spam_settings
        WHERE guild_id = ?
        """,
        (message.guild.id,)
    ).fetchone()

    if setting and setting["enabled"]:
        key = (message.guild.id, message.author.id)
        now = time.time()

        spam_messages[key].append(now)

        while (
            spam_messages[key]
            and now - spam_messages[key][0] > setting["seconds"]
        ):
            spam_messages[key].popleft()

        if len(spam_messages[key]) >= setting["max_count"]:
            try:
                await message.delete()

                await message.channel.send(
                    f"🛡️ {message.author.mention} "
                    f"スパム保護によりメッセージを削除しました。",
                    delete_after=5
                )

            except discord.HTTPException:
                pass

            spam_messages[key].clear()
            return

    await bot.process_commands(message)


# -------------------------
# コマンド一覧
# -------------------------

@bot.command(name="コマンド一覧")
async def command_list(ctx):
    text = """
📖 **たかbot コマンド一覧**

💰 **お金**
`t残高`
`tお金確認 @ユーザー`
`tデイリー`
`tランキング`

🏢 **会社**
`t会社設立 <給料> <会社名>`
`t入社 @社長`
`t退社`
`t出勤`
`t働く`
`t退勤`
`tタイムカード`
`t会社撤去`

🛡️ **管理**
`t警告 @ユーザー [件数] [理由]`
`t警告リセット @ユーザー`
`t警告を減らす @ユーザー [件数]`
`t禁止ワード <単語>`
`t禁止ワード削除 <単語>`
`tスパム保護 <有効/無効> [件数] [秒数]`

⚙️ **スラッシュコマンド**
`/bot-admin-add`
`/bot-admin-remove`
`/announcement-add`
`/announcement-remove`
`/money-add`
"""

    await ctx.send(text)


# =========================
# Bot管理者
# =========================

@bot.tree.command(
    name="bot-admin-add",
    description="Bot管理者を追加します"
)
@app_commands.describe(user="追加するユーザー")
async def bot_admin_add(
    interaction: discord.Interaction,
    user: discord.Member
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ サーバー管理者のみ実行できます。",
            ephemeral=True
        )
        return

    db.execute(
        """
        INSERT OR IGNORE INTO bot_admins
        (guild_id, user_id)
        VALUES (?, ?)
        """,
        (interaction.guild_id, user.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"✅ {user.mention} をBot管理者に追加しました。"
    )


@bot.tree.command(
    name="bot-admin-remove",
    description="Bot管理者を削除します"
)
@app_commands.describe(user="削除するユーザー")
async def bot_admin_remove(
    interaction: discord.Interaction,
    user: discord.Member
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ サーバー管理者のみ実行できます。",
            ephemeral=True
        )
        return

    db.execute(
        """
        DELETE FROM bot_admins
        WHERE guild_id = ? AND user_id = ?
        """,
        (interaction.guild_id, user.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"✅ {user.mention} をBot管理者から削除しました。"
    )


# =========================
# アナウンス権限
# =========================

@bot.tree.command(
    name="announcement-add",
    description="アナウンス権限を付与します"
)
@app_commands.describe(user="権限を付与するユーザー")
async def announcement_add(
    interaction: discord.Interaction,
    user: discord.Member
):
    if not (
        interaction.user.guild_permissions.administrator
        or is_bot_admin(interaction.guild_id, interaction.user.id)
    ):
        await interaction.response.send_message(
            "❌ 権限がありません。",
            ephemeral=True
        )
        return

    db.execute(
        """
        INSERT OR IGNORE INTO announcement_perms
        (guild_id, user_id)
        VALUES (?, ?)
        """,
        (interaction.guild_id, user.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"📢 {user.mention} にアナウンス権限を付与しました。"
    )


@bot.tree.command(
    name="announcement-remove",
    description="アナウンス権限を削除します"
)
@app_commands.describe(user="権限を削除するユーザー")
async def announcement_remove(
    interaction: discord.Interaction,
    user: discord.Member
):
    if not (
        interaction.user.guild_permissions.administrator
        or is_bot_admin(interaction.guild_id, interaction.user.id)
    ):
        await interaction.response.send_message(
            "❌ 権限がありません。",
            ephemeral=True
        )
        return

    db.execute(
        """
        DELETE FROM announcement_perms
        WHERE guild_id = ? AND user_id = ?
        """,
        (interaction.guild_id, user.id)
    )
    db.commit()

    await interaction.response.send_message(
        f"📢 {user.mention} のアナウンス権限を削除しました。"
    )


# =========================
# お金追加
# =========================

@bot.tree.command(
    name="money-add",
    description="ユーザーにお金を追加します"
)
@app_commands.describe(
    user="お金を追加するユーザー",
    amount="追加する金額"
)
async def money_add(
    interaction: discord.Interaction,
    user: discord.Member,
    amount: int
):
    if not (
        interaction.user.guild_permissions.administrator
        or is_bot_admin(interaction.guild_id, interaction.user.id)
    ):
        await interaction.response.send_message(
            "❌ 権限がありません。",
            ephemeral=True
        )
        return

    if amount <= 0:
        await interaction.response.send_message(
            "❌ 金額は1以上にしてください。",
            ephemeral=True
        )
        return

    add_money(user.id, amount)

    await interaction.response.send_message(
        f"💰 {user.mention} に **{amount:,} coins** 追加しました。"
    )


# =========================
# エラーハンドリング
# =========================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ このコマンドを実行する権限がありません。")
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "❌ 引数が足りません。\n"
            "`tコマンド一覧` で使い方を確認してください。"
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ 引数が正しくありません。\n"
            "ユーザー指定や数字を確認してください。"
        )
        return

    print("Command error:", error)


# =========================
# Bot起動
# =========================

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN が設定されていません。"
    )

bot.run(TOKEN)