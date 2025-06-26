import discord
from discord.ext import commands
import sqlite3
import asyncio # 制限時間のために必要
import configparser
import argparse


"""
"""

# discord.py 2.0以降が必要
intents = discord.Intents.default()
intents.message_content = True # メッセージ内容へのアクセスが必要な場合
bot = commands.Bot(command_prefix='/', intents=intents)

# 現在進行中のゲームの情報を保持する辞書
# 例えば、ラウンド数、現在の楽曲ID、各プレイヤーのスコアなど
active_games = {} # {guild_id: {"current_song_id": None, "scores": {}, "round": 0, "answering_lock": False}}

# コマンドライン引数でiniファイルのパスを指定
parser = argparse.ArgumentParser()
parser.add_argument('--config', type=str, default='config.ini', help='設定ファイル(.ini)のパス')
args, unknown = parser.parse_known_args()

config_ini = configparser.ConfigParser()
config_ini.read(args.config, encoding='utf-8')
DB_PATH = config_ini.get('DEFAULT', 'db_path', fallback='songs.db')
song_ids_str = config_ini.get('DEFAULT', 'song_ids', fallback=None)
SONG_IDS = [int(s.strip()) for s in song_ids_str.split(',')] if song_ids_str else None
BOT_TOKEN = config_ini.get('DEFAULT', 'bot_token')
ROUNDS = config_ini.getint('DEFAULT', 'rounds', fallback=5)
LOG_PATH = config_ini.get('DEFAULT', 'log_path', fallback='score_log.txt')
ANSWER_SECONDS = config_ini.getint('DEFAULT', 'answer_seconds', fallback=15)

@bot.command()
async def start(ctx):
    guild_id = ctx.guild.id
    if guild_id in active_games and active_games[guild_id]["current_song_id"] is not None:
        await ctx.send("現在、イントロドンが進行中です。")
        return
    active_games[guild_id] = {
        "current_song_id": None,
        "scores": {member.id: 0 for member in ctx.guild.members if not member.bot},
        "round": 0,
        "answering_lock": True,  # 最初はロック
        "question_sent": False
    }
    await ctx.send("イントロドンを開始します!\n/next で1問目を出題してください。")

@bot.command()
async def next(ctx):
    guild_id = ctx.guild.id
    if guild_id not in active_games:
        await ctx.send("現在アクティブなゲームはありません。/start で開始してください。")
        return
    game_state = active_games[guild_id]
    if game_state["round"] >= ROUNDS:
        await end_game(ctx)
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        song_info = None
        if SONG_IDS and len(SONG_IDS) > game_state["round"]:
            song_id = SONG_IDS[game_state["round"]]
            cursor.execute("SELECT id, title, artist, path FROM songs WHERE id = ?", (song_id,))
            song_info = cursor.fetchone()
        else:
            cursor.execute("SELECT id, title, artist, path FROM songs ORDER BY RANDOM() LIMIT 1")
            song_info = cursor.fetchone()
        if not song_info:
            await ctx.send("楽曲が見つかりませんでした。イントロドンを終了します。")
            del active_games[guild_id]
            conn.close()
            return
        song_id, correct_title, correct_artist, file_path = song_info
        game_state["current_song_id"] = song_id
        game_state["correct_answer_artist"] = correct_artist
        game_state["file_path"] = file_path
        game_state["answered_users"] = []
        game_state["question_sent"] = False
        answer_key = f"answer_{game_state['round']+1}"
        if config_ini.has_option('DEFAULT', answer_key):
            game_state["correct_answer_title"] = config_ini.get('DEFAULT', answer_key).strip()
        else:
            game_state["correct_answer_title"] = correct_title
    except Exception as e:
        await ctx.send(f"データベースエラー: {e}")
        if 'conn' in locals():
            conn.close()
        del active_games[guild_id]
        return
    conn.close()
    await ctx.send(f"**--- 第{game_state['round']+1}ラウンド ---**")
    # 音声ファイル送信
    try:
        await ctx.send(file=discord.File(game_state["file_path"], filename="secret.mp3"))
    except Exception as e:
        await ctx.send(f"音声ファイル送信エラー: {e}")
        game_state["current_song_id"] = None
        return
    question_key = f"question_{game_state['round']+1}"
    if config_ini.has_option('DEFAULT', question_key):
        question_text = config_ini.get('DEFAULT', question_key)
        await ctx.send(question_text)
    else:
        await ctx.send("⬆️ 曲名は何でしょう？")
    choices_key = f"choices_{game_state['round']+1}"
    if config_ini.has_option('DEFAULT', choices_key):
        options = [s.strip() for s in config_ini.get('DEFAULT', choices_key).split(',')]
    else:
        try:
            options = generate_options(game_state["correct_answer_title"])
        except Exception as e:
            await ctx.send(f"選択肢生成エラー: {e}")
            del active_games[guild_id]
            return
    buttons = []
    for i, opt in enumerate(options):
        buttons.append(discord.ui.Button(label=opt, style=discord.ButtonStyle.primary, custom_id=f"introdon_answer_{opt}"))
    view = discord.ui.View()
    for button in buttons:
        view.add_item(button)
    await ctx.send("選択肢を選んでください:", view=view)
    game_state["answering_lock"] = False
    game_state["question_sent"] = True
    async def timer_and_close():
        await asyncio.sleep(ANSWER_SECONDS)
        game_state["answering_lock"] = True
        await announce_round_results(ctx, game_state)
        if game_state["round"] >= ROUNDS:
            await end_game(ctx)
        else:
            await ctx.send("/next で次の問題を出題してください。")
    asyncio.create_task(timer_and_close())
    game_state["round"] += 1

def generate_options(correct_answer: str):
    options = [correct_answer]
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT title FROM songs WHERE title != ? ORDER BY RANDOM() LIMIT 3", (correct_answer,))
        for row in cursor.fetchall():
            options.append(row[0])
        conn.close()
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        raise e
    import random
    random.shuffle(options)
    return options

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type.name == "component":
        custom_id = interaction.data.get('custom_id')
        if custom_id and custom_id.startswith("introdon_answer_"):
            guild_id = interaction.guild.id
            if guild_id not in active_games or active_games[guild_id]["answering_lock"]:
                await interaction.response.send_message("回答期間は終了しました。", ephemeral=True)
                return
            if interaction.user.id in active_games[guild_id]["answered_users"]:
                await interaction.response.send_message("このラウンドでは既に回答済みです。", ephemeral=True)
                return

            selected_answer = custom_id.replace("introdon_answer_", "")
            correct_title = active_games[guild_id]["correct_answer_title"]
            user_id = interaction.user.id

            if user_id not in active_games[guild_id]["scores"]:
                active_games[guild_id]["scores"][user_id] = 0

            active_games[guild_id]["answered_users"].append(user_id)

            if selected_answer == correct_title:
                active_games[guild_id]["scores"][user_id] += 1
                # await interaction.response.send_message("正解！", ephemeral=True)
                await interaction.response.send_message("回答済み", ephemeral=True)
            else:
                # await interaction.response.send_message("残念、不正解。", ephemeral=True)
                await interaction.response.send_message("回答済み", ephemeral=True)


async def announce_round_results(ctx, game_state):
    correct_title = game_state["correct_answer_title"]
    await ctx.send(f"正解は「{correct_title}」でした！")

def log_score(guild_id, sorted_scores, ended=False, round_num=None):
    import datetime
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'[{now}] guild_id={guild_id} {"最終結果" if ended else "途中経過"}')
        if round_num is not None:
            f.write(f' 第{round_num}問')
        f.write('\n')
        for i, (user_id, score) in enumerate(sorted_scores, 1):
            try:
                user = asyncio.get_event_loop().run_until_complete(bot.fetch_user(user_id))
                name = user.display_name
            except Exception:
                name = f"ユーザーID:{user_id}"
            f.write(f'{i}位: {name} ({score}点)\n')
        f.write('\n')
        f.write('--------------------------------\n')

@bot.command()
async def score(ctx):
    guild_id = ctx.guild.id
    if guild_id not in active_games:
        await ctx.send("現在アクティブなゲームはありません。/start で開始してください。")
        return
    game_state = active_games[guild_id]
    sorted_scores = sorted(game_state["scores"].items(), key=lambda item: item[1], reverse=True)
    round_num = game_state["round"]
    if game_state.get("game_ended"):
        ranking_msg = "**--- 最終順位 ---**\n"
        rank = 1
        prev_score = -1
        for i, (user_id, score) in enumerate(sorted_scores):
            try:
                user = await bot.fetch_user(user_id)
                if score < prev_score:
                    rank = i + 1
                ranking_msg += f"{rank}位: {user.display_name} ({score}点)\n"
                prev_score = score
            except Exception:
                ranking_msg += f"{rank}位: ユーザーID:{user_id} ({score}点)\n"
                prev_score = score
        await ctx.send(ranking_msg)
        log_score(guild_id, sorted_scores, ended=True, round_num=round_num)
        del active_games[guild_id]
    else:
        scoreboard_msg = "**--- 現在のスコア ---**\n"
        for user_id, score in sorted_scores:
            try:
                user = await bot.fetch_user(user_id)
                scoreboard_msg += f"{user.display_name}: {score}点\n"
            except Exception:
                scoreboard_msg += f"ユーザーID:{user_id}: {score}点\n"
        await ctx.send(scoreboard_msg)
        log_score(guild_id, sorted_scores, ended=False, round_num=round_num)

async def end_game(ctx):
    guild_id = ctx.guild.id
    game_state = active_games[guild_id]

    await ctx.send("**--- イントロドン終了！ ---**")
    await ctx.send("/score で最終順位を確認できます。")
    game_state["game_ended"] = True  # 終了フラグを立てる
    # 最終順位の送信やactive_gamesからの削除は/scoreで行う

if __name__ == '__main__':
    bot.run(BOT_TOKEN)