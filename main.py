import discord
from discord.ext import commands
import configparser
import argparse
from game_manager import GameManager
from command_handler import CommandHandler

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

# チャンネル設定の取得
GAME_CHANNEL_ID = config_ini.getint('DEFAULT', 'game_channel_id', fallback=None)
COMMAND_CHANNEL_ID = config_ini.getint('DEFAULT', 'command_channel_id', fallback=None)

# サーバー設定の取得
GAME_GUILD_ID = config_ini.getint('DEFAULT', 'game_guild_id', fallback=None)
COMMAND_GUILD_ID = config_ini.getint('DEFAULT', 'command_guild_id', fallback=None)

# Discord Botの初期化
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# ゲームマネージャーとコマンドハンドラーの初期化
game_manager = GameManager(bot, config_ini, DB_PATH, LOG_PATH, ROUNDS, SONG_IDS, ANSWER_SECONDS)
command_handler = CommandHandler(bot, game_manager, GAME_GUILD_ID, COMMAND_GUILD_ID, GAME_CHANNEL_ID, COMMAND_CHANNEL_ID)

# GameManagerにCommandHandlerの参照を設定
game_manager.command_handler = command_handler

# コマンド定義
@bot.command()
async def start(ctx):
    await command_handler.handle_start_command(ctx)

@bot.command()
async def next(ctx):
    await command_handler.handle_next_command(ctx)

@bot.command()
async def answer(ctx):
    await command_handler.handle_answer_command(ctx)

@bot.command()
async def score(ctx):
    await command_handler.handle_score_command(ctx)

# ボタンのインタラクション処理
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type.name == "component":
        custom_id = interaction.data.get('custom_id')
        
        # コマンドボタンの処理
        if custom_id and custom_id.startswith("cmd_"):
            # コマンドサーバー権限チェック
            if COMMAND_GUILD_ID is not None and interaction.guild.id != COMMAND_GUILD_ID:
                await interaction.response.send_message("このサーバーではコマンドを実行できません。", ephemeral=True)
                return
            
            # コマンドチャンネル権限チェック
            if COMMAND_CHANNEL_ID is not None and interaction.channel.id != COMMAND_CHANNEL_ID:
                await interaction.response.send_message("このチャンネルではコマンドを実行できません。", ephemeral=True)
                return
            
            # コマンドの実行
            command = custom_id.replace("cmd_", "")
            game_guild_id = GAME_GUILD_ID or interaction.guild.id
            
            if command == "start":
                # ゲーム開始処理
                
                # ゲーム進行中かどうかをチェック
                if game_manager.is_game_active(game_guild_id):
                    await interaction.response.send_message("現在ゲームが進行中です。ラウンドが終了するまでお待ちください。", ephemeral=True)
                    return
                
                if game_manager.get_game_state(game_guild_id) and game_manager.get_game_state(game_guild_id)["current_song_id"] is not None:
                    await interaction.response.send_message("現在、クイズが進行中です。", ephemeral=True)
                    return
                
                # ゲームサーバーのメンバー情報を取得
                if GAME_GUILD_ID is not None:
                    game_guild = bot.get_guild(GAME_GUILD_ID)
                    if game_guild:
                        members = game_guild.members
                    else:
                        await interaction.response.send_message("ゲームサーバーが見つかりません。", ephemeral=True)
                        return
                else:
                    members = interaction.guild.members
                
                # ゲーム状態を初期化
                await game_manager.start_game(game_guild_id, members)
                
                # ゲーム開始メッセージをゲームチャンネルに送信
                game_guild = bot.get_guild(GAME_GUILD_ID) if GAME_GUILD_ID else interaction.guild
                game_channel = game_guild.get_channel(GAME_CHANNEL_ID) if GAME_CHANNEL_ID else interaction.channel
                if game_channel:
                    await game_channel.send("楽曲クイズを始めるわよ！")
                
                await interaction.response.send_message("ゲームを開始しました。", ephemeral=True, delete_after=5.0)
                
            elif command == "next":
                # 次の問題出題処理
                
                # 問題出題中かどうかをチェック
                if game_manager.is_question_active(game_guild_id):
                    await interaction.response.send_message("現在問題が出題中です。回答時間が終了するまでお待ちください。", ephemeral=True)
                    return
                
                # 回答時間終了後で正解未発表の状態かどうかをチェック
                if game_manager.is_waiting_for_answer(game_guild_id):
                    await interaction.response.send_message("回答時間が終了しました。正解を発表してから次の問題を出題してください。", ephemeral=True)
                    return
                
                if not game_manager.get_game_state(game_guild_id):
                    await interaction.response.send_message("現在アクティブなゲームはありません。/start で開始してください。", ephemeral=True, delete_after=5.0)
                    return
                
                game_state = game_manager.get_game_state(game_guild_id)
                
                # ゲームチャンネルを取得
                if GAME_GUILD_ID is not None:
                    game_guild = bot.get_guild(GAME_GUILD_ID)
                    game_channel = game_guild.get_channel(GAME_CHANNEL_ID) if game_guild else None
                else:
                    game_channel = interaction.channel
                
                if not game_channel:
                    await interaction.response.send_message("ゲームチャンネルが見つかりません。", ephemeral=True)
                    return
                
                # 次の問題を出題
                success = await game_manager.next_question(game_guild_id, game_channel, game_state)
                
                if success:
                    await interaction.response.send_message("問題を出題しました。", ephemeral=True, delete_after=5.0)
                else:
                    await interaction.response.send_message("問題の出題に失敗しました。", ephemeral=True)
                
            elif command == "answer":
                # 正解発表処理
                if not game_manager.get_game_state(game_guild_id):
                    await interaction.response.send_message("現在アクティブなゲームはありません。", ephemeral=True)
                    return
                
                game_state = game_manager.get_game_state(game_guild_id)
                if game_state["current_song_id"] is None:
                    await interaction.response.send_message("現在出題中の問題はありません。", ephemeral=True)
                    return
                
                # 正解情報を取得
                correct_title = game_state.get("correct_answer_title", "不明")
                correct_artist = game_state.get("correct_answer_artist", "不明")
                
                # 正解発表をコンソールに出力
                game_manager.log_answer(game_guild_id, correct_title, correct_artist)
                
                # 正解メッセージを作成
                answer_msg = f"**正解発表！**\n"
                answer_msg += f"曲名: {correct_title}\n"
                answer_msg += f"アーティスト: {correct_artist}"
                
                # ゲームチャンネルに正解を送信
                game_guild = bot.get_guild(GAME_GUILD_ID) if GAME_GUILD_ID else interaction.guild
                game_channel = game_guild.get_channel(GAME_CHANNEL_ID) if GAME_CHANNEL_ID else interaction.channel
                if game_channel:
                    await game_channel.send(answer_msg)
                
                # 正解発表後にゲーム状態を更新（次の問題の準備）
                game_state["current_song_id"] = None
                game_state["question_sent"] = False
                
                # コマンドボタンを更新（確実に実行）
                await command_handler.update_command_buttons(game_guild_id)
                
                await interaction.response.send_message("正解をゲームチャンネルに送信しました。", ephemeral=True, delete_after=5.0)
                
            elif command == "score":
                # スコア表示処理
                if not game_manager.get_game_state(game_guild_id):
                    await interaction.response.send_message("現在アクティブなゲームはありません。", ephemeral=True, delete_after=5.0)
                    return
                
                game_state = game_manager.get_game_state(game_guild_id)
                sorted_scores = sorted(game_state["scores"].items(), key=lambda item: item[1], reverse=True)
                
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
                    
                    # ゲームチャンネルに送信
                    game_guild = bot.get_guild(GAME_GUILD_ID) if GAME_GUILD_ID else interaction.guild
                    game_channel = game_guild.get_channel(GAME_CHANNEL_ID) if GAME_CHANNEL_ID else interaction.channel
                    if game_channel:
                        await game_channel.send(ranking_msg)
                    
                    await interaction.response.send_message("最終結果をゲームチャンネルに送信しました。", ephemeral=True, delete_after=5.0)
                else:
                    scoreboard_msg = "**--- 現在のスコア ---**\n"
                    for user_id, score in sorted_scores:
                        try:
                            user = await bot.fetch_user(user_id)
                            scoreboard_msg += f"{user.display_name}: {score}点\n"
                        except Exception:
                            scoreboard_msg += f"ユーザーID:{user_id}: {score}点\n"
                    
                    # ゲームチャンネルに送信
                    game_guild = bot.get_guild(GAME_GUILD_ID) if GAME_GUILD_ID else interaction.guild
                    game_channel = game_guild.get_channel(GAME_CHANNEL_ID) if GAME_CHANNEL_ID else interaction.channel
                    if game_channel:
                        await game_channel.send(scoreboard_msg)
                    
                    await interaction.response.send_message("現在のスコアをゲームチャンネルに送信しました。", ephemeral=True, delete_after=5.0)
            
            return
        
        # 回答ボタンの処理
        if custom_id and custom_id.startswith("introdon_answer_"):
            # ゲームサーバー権限チェック いらないかも
            #if GAME_GUILD_ID is not None and interaction.guild.id != GAME_GUILD_ID:
            #    await interaction.response.send_message("このサーバーでは回答できません。ゲームサーバーで回答してください。", ephemeral=True, delete_after=5.0)
            #    return
            
            # ゲームチャンネル権限チェック いらないかも
            #if GAME_CHANNEL_ID is not None and interaction.channel.id != GAME_CHANNEL_ID:
            #    await interaction.response.send_message("このチャンネルでは回答できません。ゲームチャンネルで回答してください。", ephemeral=True, delete_after=5.0)
            #    return
            
            # ゲームサーバーIDを取得
            game_guild_id = GAME_GUILD_ID or interaction.guild.id
            
            if not game_manager.get_game_state(game_guild_id) or game_manager.get_game_state(game_guild_id)["answering_lock"]:
                await interaction.response.send_message("回答期間は終了しました。", ephemeral=True)
                return
            if interaction.user.id in game_manager.get_game_state(game_guild_id)["answered_users"]:
                await interaction.response.send_message("このラウンドでは既に回答済みです。", ephemeral=True)
                return
            selected_answer = custom_id.replace("introdon_answer_", "")
            correct_title = game_manager.get_game_state(game_guild_id)["correct_answer_title"]
            user_id = interaction.user.id
            if user_id not in game_manager.get_game_state(game_guild_id)["scores"]:
                game_manager.get_game_state(game_guild_id)["scores"][user_id] = 0
            game_manager.get_game_state(game_guild_id)["answered_users"].append(user_id)
            # 正誤判定を送る場合はコメントアウトを切り替え
            if selected_answer == correct_title:
                game_manager.get_game_state(game_guild_id)["scores"][user_id] += 1
                # await interaction.response.send_message("正解！", ephemeral=True)
                await interaction.response.send_message("回答済み", ephemeral=True)
            else:
                # await interaction.response.send_message("残念、不正解。", ephemeral=True)
                await interaction.response.send_message("回答済み", ephemeral=True)

# Bot起動
if __name__ == '__main__':
    @bot.event
    async def on_ready():
        print(f'{bot.user} としてログインしました')
        
        # コマンド用サーバーにメッセージを送信
        if COMMAND_GUILD_ID is not None and COMMAND_CHANNEL_ID is not None:
            command_guild = bot.get_guild(COMMAND_GUILD_ID)
            if command_guild:
                command_channel = command_guild.get_channel(COMMAND_CHANNEL_ID)
                if command_channel:
                    # コマンドボタンを表示
                    command_view = command_handler.create_command_buttons()
                    await command_channel.send("🎵 **音楽クイズボットが起動しました！**\n\n**開始**ボタンを押してゲームを開始してください。", view=command_view)
                    print(f"コマンド用チャンネル {command_channel.name} にメッセージを送信しました")
                else:
                    print(f"コマンド用チャンネルが見つかりません: {COMMAND_CHANNEL_ID}")
            else:
                print(f"コマンド用サーバーが見つかりません: {COMMAND_GUILD_ID}")
        else:
            print("コマンド用サーバーまたはチャンネルが設定されていません")
    
    bot.run(BOT_TOKEN)