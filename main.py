import discord
from discord.ext import commands
import sqlite3
import asyncio  # 制限時間のために必要
import configparser
import argparse

# 設定ファイルの読み込み
parser = argparse.ArgumentParser()
parser.add_argument('--config', type=str, default='config.ini', help='設定ファイル(.ini)のパス')
args, unknown = parser.parse_known_args()

config_ini = configparser.ConfigParser()
config_ini.read(args.config, encoding='utf-8')

# 設定値の取得
DB_PATH = config_ini.get('DEFAULT', 'db_path', fallback='songs.db')
song_ids_str = config_ini.get('DEFAULT', 'song_ids', fallback=None)
SONG_IDS = [int(s.strip()) for s in song_ids_str.split(',')] if song_ids_str else None
BOT_TOKEN = config_ini.get('DEFAULT', 'bot_token')
ROUNDS = config_ini.getint('DEFAULT', 'rounds', fallback=5)
LOG_PATH = config_ini.get('DEFAULT', 'log_path', fallback='score_log.txt')
ANSWER_SECONDS = config_ini.getint('DEFAULT', 'answer_seconds', fallback=15)

# Discord Botの初期化
intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容へのアクセスが必要
bot = commands.Bot(command_prefix='/', intents=intents)

# ゲームの進行状況を保持する辞書
active_games = {}  # {guild_id: {...}}

# /startコマンド: ゲーム開始
@bot.command()
async def start(ctx):
    guild_id = ctx.guild.id
    # すでにゲームが進行中なら拒否
    if guild_id in active_games and active_games[guild_id]["current_song_id"] is not None:
        await ctx.send("現在、クイズが進行中です。")
        return
    # ゲーム状態を初期化
    active_games[guild_id] = {
        "current_song_id": None,
        "scores": {member.id: 0 for member in ctx.guild.members if not member.bot},
        "round": 0,
        "answering_lock": True,  # 最初はロック
        "question_sent": False
    }
    await ctx.send("楽曲クイズを開始します!\n/next で1問目を出題してください。")

# /nextコマンド: 次の問題を出題
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
        # 楽曲情報をDBから取得
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
            await ctx.send("楽曲が見つかりませんでした。クイズを終了します。")
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

    # ラウンド開始メッセージ
    await ctx.send(f"**--- 第{game_state['round']+1}ラウンド ---**")

    # 音声ファイル送信（ファイル名はsecret.mp3で統一）
    try:
        await ctx.send(file=discord.File(game_state["file_path"], filename="secret.mp3"))
    except Exception as e:
        await ctx.send(f"音声ファイル送信エラー: {e}")
        game_state["current_song_id"] = None
        return

    # 問題文の送信（iniで指定がなければデフォルト）
    question_key = f"question_{game_state['round']+1}"
    if config_ini.has_option('DEFAULT', question_key):
        question_text = config_ini.get('DEFAULT', question_key)
        await ctx.send(question_text)
    else:
        await ctx.send("⬆️ 曲名は何でしょう？")

    # 選択肢の生成
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
    # ボタン生成
    buttons = []
    for i, opt in enumerate(options):
        buttons.append(discord.ui.Button(label=opt, style=discord.ButtonStyle.primary, custom_id=f"introdon_answer_{opt}"))
    view = discord.ui.View()
    for button in buttons:
        view.add_item(button)
    await ctx.send("選択肢を選んでください:", view=view)

    # 回答受付開始
    game_state["answering_lock"] = False
    game_state["question_sent"] = True

    # 回答時間終了後の処理
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

# 選択肢をDBからランダム生成
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

# ボタンのインタラクション処理
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
            # 正誤判定を送る場合はコメントアウトを切り替え
            if selected_answer == correct_title:
                active_games[guild_id]["scores"][user_id] += 1
                # await interaction.response.send_message("正解！", ephemeral=True)
                await interaction.response.send_message("回答済み", ephemeral=True)
            else:
                # await interaction.response.send_message("残念、不正解。", ephemeral=True)
                await interaction.response.send_message("回答済み", ephemeral=True)

# ラウンド終了時の正解発表・スコアログ出力
async def announce_round_results(ctx, game_state):
    correct_title = game_state["correct_answer_title"]
    await ctx.send(f"正解は「{correct_title}」でした！")
    sorted_scores = sorted(game_state["scores"].items(), key=lambda item: item[1], reverse=True)
    round_num = game_state["round"]
    await log_score(ctx, ctx.guild.id, sorted_scores, ended=False, round_num=round_num)

# スコアログ出力（ctx, guild, display_name対応）
async def log_score(ctx, guild_id, sorted_scores, ended=False, round_num=None):
    import datetime
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'[{now}] guild_id={guild_id} {"最終結果" if ended else "途中経過"}')
        if round_num is not None:
            f.write(f' 第{round_num}問')
        f.write('\n')
        for i, (user_id, score) in enumerate(sorted_scores, 1):
            member = ctx.guild.get_member(user_id)
            if member:
                name = member.display_name
            else:
                try:
                    user = await bot.fetch_user(user_id)
                    name = user.name
                except Exception:
                    name = f"ユーザーID:{user_id}"
            f.write(f'{i}位: {name} ({score}点)\n')
        f.write('\n')
        f.write('--------------------------------\n')

# /scoreコマンド: 現在のスコアまたは最終順位を表示
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
        await log_score(ctx, guild_id, sorted_scores, ended=True, round_num=round_num)
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
        await log_score(ctx, guild_id, sorted_scores, ended=False, round_num=round_num)

# ゲーム終了処理
async def end_game(ctx):
    guild_id = ctx.guild.id
    game_state = active_games[guild_id]
    await ctx.send("**--- クイズ終了！ ---**")
    await ctx.send("/score で最終順位を確認できます。")
    game_state["game_ended"] = True  # 終了フラグを立てる
    # 最終順位の送信やactive_gamesからの削除は/scoreで行う

# Bot起動
if __name__ == '__main__':
    bot.run(BOT_TOKEN)