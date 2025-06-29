import discord
import sqlite3
import asyncio
import configparser
import datetime
import random

class GameManager:
    def __init__(self, bot, config_ini, db_path, log_path, rounds, song_ids, answer_seconds):
        self.bot = bot
        self.config_ini = config_ini
        self.db_path = db_path
        self.log_path = log_path
        self.rounds = rounds
        self.song_ids = song_ids
        self.answer_seconds = answer_seconds
        self.active_games = {}  # {game_guild_id: {...}}
    
    def get_game_guild_id(self, game_guild_id):
        """ゲームサーバーIDを取得（設定されていない場合はNone）"""
        return game_guild_id
    
    def generate_options(self, correct_answer: str):
        """選択肢をDBからランダム生成"""
        options = [correct_answer]
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT title FROM songs WHERE title != ? ORDER BY RANDOM() LIMIT 3", (correct_answer,))
            for row in cursor.fetchall():
                options.append(row[0])
            conn.close()
        except Exception as e:
            if 'conn' in locals():
                conn.close()
            raise e
        random.shuffle(options)
        return options
    
    async def start_game(self, guild_id, members):
        """ゲーム開始"""
        self.active_games[guild_id] = {
            "current_song_id": None,
            "scores": {member.id: 0 for member in members if not member.bot},
            "round": 0,
            "answering_lock": True,
            "question_sent": False
        }
    
    async def next_question(self, game_guild_id, game_channel, game_state):
        """次の問題を出題"""
        if game_state["round"] >= self.rounds:
            # ゲーム終了処理
            await game_channel.send("**--- クイズ終了！ ---**")
            game_state["game_ended"] = True
            return False
        
        try:
            # 楽曲情報をDBから取得
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            song_info = None
            if self.song_ids and len(self.song_ids) > game_state["round"]:
                song_id = self.song_ids[game_state["round"]]
                cursor.execute("SELECT id, title, artist, path FROM songs WHERE id = ?", (song_id,))
                song_info = cursor.fetchone()
            else:
                cursor.execute("SELECT id, title, artist, path FROM songs ORDER BY RANDOM() LIMIT 1")
                song_info = cursor.fetchone()
            if not song_info:
                await game_channel.send("楽曲が見つかりませんでした。クイズを終了します。")
                del self.active_games[game_guild_id]
                conn.close()
                return False
            song_id, correct_title, correct_artist, file_path = song_info
            game_state["current_song_id"] = song_id
            game_state["correct_answer_artist"] = correct_artist
            game_state["file_path"] = file_path
            game_state["answered_users"] = []
            game_state["question_sent"] = False
            answer_key = f"answer_{game_state['round']+1}"
            if self.config_ini.has_option('DEFAULT', answer_key):
                game_state["correct_answer_title"] = self.config_ini.get('DEFAULT', answer_key).strip()
            else:
                game_state["correct_answer_title"] = correct_title
        except Exception as e:
            await game_channel.send(f"データベースエラー: {e}")
            if 'conn' in locals():
                conn.close()
            del self.active_games[game_guild_id]
            return False
        conn.close()

        # ラウンド開始メッセージ
        await game_channel.send(f"**--- 第{game_state['round']+1}ラウンド ---**")

        # 音声ファイル送信
        try:
            await game_channel.send(file=discord.File(game_state["file_path"], filename="secret.mp3"))
        except Exception as e:
            await game_channel.send(f"音声ファイル送信エラー: {e}")
            game_state["current_song_id"] = None
            return False

        # 問題文の送信
        question_key = f"question_{game_state['round']+1}"
        if self.config_ini.has_option('DEFAULT', question_key):
            question_text = self.config_ini.get('DEFAULT', question_key)
            await game_channel.send(question_text)
        else:
            await game_channel.send("⬆️ 曲名は何でしょう？")

        # 選択肢の生成
        choices_key = f"choices_{game_state['round']+1}"
        if self.config_ini.has_option('DEFAULT', choices_key):
            options = [s.strip() for s in self.config_ini.get('DEFAULT', choices_key).split(',')]
        else:
            try:
                options = self.generate_options(game_state["correct_answer_title"])
            except Exception as e:
                await game_channel.send(f"選択肢生成エラー: {e}")
                del self.active_games[game_guild_id]
                return False
        
        # ボタン生成
        buttons = []
        for i, opt in enumerate(options):
            buttons.append(discord.ui.Button(label=opt, style=discord.ButtonStyle.primary, custom_id=f"introdon_answer_{opt}"))
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)
        await game_channel.send("選択肢を選んでください:", view=view)

        # 回答受付開始
        game_state["answering_lock"] = False
        game_state["question_sent"] = True

        # 回答時間終了後の処理
        async def timer_and_close():
            await asyncio.sleep(self.answer_seconds)
            game_state["answering_lock"] = True
            await self.announce_round_results(game_guild_id, game_state)
            if game_state["round"] >= self.rounds:
                await game_channel.send("**--- クイズ終了！ ---**")
                game_state["game_ended"] = True
            else:
                await game_channel.send("回答終了！！！")
        
        asyncio.create_task(timer_and_close())
        game_state["round"] += 1
        return True
    
    async def announce_round_results(self, guild_id, game_state):
        """ラウンド終了時のスコアログ出力"""
        sorted_scores = sorted(game_state["scores"].items(), key=lambda item: item[1], reverse=True)
        round_num = game_state["round"]
        await self.log_score(guild_id, sorted_scores, ended=False, round_num=round_num)
    
    async def log_score(self, guild_id, sorted_scores, ended=False, round_num=None):
        """スコアログ出力"""
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(f'[{now}] guild_id={guild_id} {"最終結果" if ended else "途中経過"}')
            if round_num is not None:
                f.write(f' 第{round_num}問')
            f.write('\n')
            for i, (user_id, score) in enumerate(sorted_scores, 1):
                try:
                    user = await self.bot.fetch_user(user_id)
                    name = user.display_name
                except Exception:
                    name = f"ユーザーID:{user_id}"
                f.write(f'{i}位: {name} ({score}点)\n')
            f.write('\n')
            f.write('--------------------------------\n')
    
    def get_game_state(self, guild_id):
        """ゲーム状態を取得"""
        return self.active_games.get(guild_id)
    
    def end_game(self, guild_id):
        """ゲーム終了"""
        if guild_id in self.active_games:
            del self.active_games[guild_id] 