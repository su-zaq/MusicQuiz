import discord
from discord.ext import commands

class CommandHandler:
    def __init__(self, bot, game_manager, game_guild_id, command_guild_id, game_channel_id, command_channel_id):
        self.bot = bot
        self.game_manager = game_manager
        self.game_guild_id = game_guild_id
        self.command_guild_id = command_guild_id
        self.game_channel_id = game_channel_id
        self.command_channel_id = command_channel_id
    
    async def check_guild_permission(self, ctx, required_guild_id, guild_type):
        """指定されたサーバーでのみコマンドを実行可能にする"""
        if required_guild_id is None:
            return True  # 設定されていない場合は制限なし
        
        if ctx.guild.id != required_guild_id:
            await ctx.send(f"このコマンドは{guild_type}サーバーでのみ実行できます。", delete_after=5.0)
            return False
        return True
    
    async def check_channel_permission(self, ctx, required_channel_id, channel_type):
        """指定されたチャンネルでのみコマンドを実行可能にする"""
        if required_channel_id is None:
            return True  # 設定されていない場合は制限なし
        
        if ctx.channel.id != required_channel_id:
            await ctx.send(f"このコマンドは{channel_type}チャンネルでのみ実行できます。", delete_after=5.0)
            return False
        return True
    
    async def send_to_game_channel(self, ctx, message, file=None, view=None):
        """ゲーム用サーバーとチャンネルにメッセージを送信"""
        if self.game_guild_id is None or self.game_channel_id is None:
            # ゲームサーバーまたはチャンネルが設定されていない場合は現在のチャンネルに送信
            if file:
                await ctx.send(message, file=file, view=view)
            else:
                await ctx.send(message, view=view)
        else:
            # ゲームサーバーとチャンネルに送信
            game_guild = self.bot.get_guild(self.game_guild_id)
            if game_guild:
                game_channel = game_guild.get_channel(self.game_channel_id)
                if game_channel:
                    if file:
                        await game_channel.send(message, file=file, view=view)
                    else:
                        await game_channel.send(message, view=view)
                else:
                    await ctx.send("ゲームチャンネルが見つかりません。", delete_after=5.0)
            else:
                await ctx.send("ゲームサーバーが見つかりません。", delete_after=5.0)
    
    def create_command_buttons(self):
        """コマンド用のボタンを作成"""
        buttons = [
            discord.ui.Button(label="開始", style=discord.ButtonStyle.success, custom_id="cmd_start"),
            discord.ui.Button(label="出題", style=discord.ButtonStyle.primary, custom_id="cmd_next"),
            discord.ui.Button(label="正解", style=discord.ButtonStyle.secondary, custom_id="cmd_answer"),
            discord.ui.Button(label="スコア", style=discord.ButtonStyle.danger, custom_id="cmd_score")
        ]
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)
        return view
    
    def create_command_buttons_disabled(self):
        """問題出題中に無効化されたコマンド用のボタンを作成"""
        buttons = [
            discord.ui.Button(label="開始", style=discord.ButtonStyle.success, custom_id="cmd_start", disabled=True),
            discord.ui.Button(label="出題", style=discord.ButtonStyle.primary, custom_id="cmd_next", disabled=True),
            discord.ui.Button(label="正解", style=discord.ButtonStyle.secondary, custom_id="cmd_answer", disabled=True),
            discord.ui.Button(label="スコア", style=discord.ButtonStyle.danger, custom_id="cmd_score", disabled=True)
        ]
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)
        return view
    
    def create_command_buttons_game_active(self):
        """ゲーム進行中に開始ボタンのみ無効化されたコマンド用のボタンを作成"""
        buttons = [
            discord.ui.Button(label="開始", style=discord.ButtonStyle.success, custom_id="cmd_start", disabled=True),
            discord.ui.Button(label="出題", style=discord.ButtonStyle.primary, custom_id="cmd_next"),
            discord.ui.Button(label="正解", style=discord.ButtonStyle.secondary, custom_id="cmd_answer"),
            discord.ui.Button(label="スコア", style=discord.ButtonStyle.danger, custom_id="cmd_score")
        ]
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)
        return view
    
    def create_command_buttons_waiting_answer(self):
        """回答時間終了後で正解未発表の状態に応じたコマンド用のボタンを作成"""
        buttons = [
            discord.ui.Button(label="開始", style=discord.ButtonStyle.success, custom_id="cmd_start", disabled=True),
            discord.ui.Button(label="出題", style=discord.ButtonStyle.primary, custom_id="cmd_next", disabled=True),
            discord.ui.Button(label="正解", style=discord.ButtonStyle.secondary, custom_id="cmd_answer"),
            discord.ui.Button(label="スコア", style=discord.ButtonStyle.danger, custom_id="cmd_score")
        ]
        view = discord.ui.View()
        for button in buttons:
            view.add_item(button)
        return view
    
    async def handle_start_command(self, ctx):
        """/startコマンドの処理"""
        # コマンドサーバー権限チェック
        if not await self.check_guild_permission(ctx, self.command_guild_id, "コマンド"):
            return
        
        # コマンドチャンネル権限チェック
        if not await self.check_channel_permission(ctx, self.command_channel_id, "コマンド"):
            return
        
        # ゲームサーバーIDを取得
        game_guild_id = self.game_guild_id or ctx.guild.id
        
        # ゲーム進行中かどうかをチェック
        if self.game_manager.is_game_active(game_guild_id):
            await ctx.send("現在ゲームが進行中です。ラウンドが終了するまでお待ちください。", delete_after=5.0)
            return
        
        # すでにゲームが進行中なら拒否
        if self.game_manager.get_game_state(game_guild_id) and self.game_manager.get_game_state(game_guild_id)["current_song_id"] is not None:
            await ctx.send("現在、クイズが進行中です。", delete_after=5.0)
            return
        
        # ゲームサーバーのメンバー情報を取得
        if self.game_guild_id is not None:
            game_guild = self.bot.get_guild(self.game_guild_id)
            if game_guild:
                members = game_guild.members
            else:
                await ctx.send("ゲームサーバーが見つかりません。", delete_after=5.0)
                return
        else:
            members = ctx.guild.members
        
        # ゲーム状態を初期化
        await self.game_manager.start_game(game_guild_id, members)
        
        # ゲーム開始メッセージをゲームチャンネルに送信
        await self.send_to_game_channel(ctx, "楽曲クイズを始めるわよ！")
        
        # コマンドボタンを表示
        command_view = self.create_command_buttons()
        await ctx.send("ゲームを開始しました。コマンドボタンを使用してください。", view=command_view, delete_after=5.0)
        
        # 元のメッセージを削除
        try:
            await ctx.message.delete()
        except:
            pass  # 削除できない場合は無視
        
        # コマンドボタンを更新
        await self.update_command_buttons(ctx.guild.id)
    
    async def handle_next_command(self, ctx):
        """/nextコマンドの処理"""
        # コマンドサーバー権限チェック
        if not await self.check_guild_permission(ctx, self.command_guild_id, "コマンド"):
            return
        
        # コマンドチャンネル権限チェック
        if not await self.check_channel_permission(ctx, self.command_channel_id, "コマンド"):
            return
        
        # ゲームサーバーIDを取得
        game_guild_id = self.game_guild_id or ctx.guild.id
        
        # 回答時間終了後で正解未発表の状態かどうかをチェック
        if self.game_manager.is_waiting_for_answer(game_guild_id):
            await ctx.send("回答時間が終了しました。正解を発表してから次の問題を出題してください。", delete_after=5.0)
            return
        
        if not self.game_manager.get_game_state(game_guild_id):
            await ctx.send("現在アクティブなゲームはありません。/start で開始してください。", delete_after=5.0)
            return
        
        game_state = self.game_manager.get_game_state(game_guild_id)
        
        # ゲームチャンネルを取得
        if self.game_guild_id is not None:
            game_guild = self.bot.get_guild(self.game_guild_id)
            game_channel = game_guild.get_channel(self.game_channel_id) if game_guild else None
        else:
            game_channel = ctx.channel
        
        if not game_channel:
            await ctx.send("ゲームチャンネルが見つかりません。")
            return
        
        # 次の問題を出題
        success = await self.game_manager.next_question(game_guild_id, game_channel, game_state)
        
        if success:
            # コマンドボタンを再表示
            command_view = self.create_command_buttons()
            await ctx.send("問題を出題しました。コマンドボタンを使用してください。", view=command_view, delete_after=5.0)
            
            # 元のメッセージを削除
            try:
                await ctx.message.delete()
            except:
                pass  # 削除できない場合は無視
            
            # コマンドボタンを更新
            await self.update_command_buttons(ctx.guild.id)
    
    async def handle_answer_command(self, ctx):
        """/answerコマンドの処理"""
        # コマンドサーバー権限チェック
        if not await self.check_guild_permission(ctx, self.command_guild_id, "コマンド"):
            return
        
        # コマンドチャンネル権限チェック
        if not await self.check_channel_permission(ctx, self.command_channel_id, "コマンド"):
            return
        
        # ゲームサーバーIDを取得
        game_guild_id = self.game_guild_id or ctx.guild.id
        
        if not self.game_manager.get_game_state(game_guild_id):
            await ctx.send("現在アクティブなゲームはありません。/start で開始してください。", delete_after=5.0)
            return
        
        game_state = self.game_manager.get_game_state(game_guild_id)
        
        if game_state["current_song_id"] is None:
            await ctx.send("現在出題中の問題はありません。", delete_after=5.0)
            return
        
        # 正解情報を取得
        correct_title = game_state.get("correct_answer_title", "不明")
        correct_artist = game_state.get("correct_answer_artist", "不明")
        
        # 正解メッセージを作成
        answer_msg = f"**正解発表！**\n"
        answer_msg += f"曲名: {correct_title}\n"
        answer_msg += f"アーティスト: {correct_artist}"
        
        # ゲームチャンネルに正解を送信
        await self.send_to_game_channel(ctx, answer_msg)
        
        # 正解発表後にゲーム状態を更新（次の問題の準備）
        game_state["current_song_id"] = None
        game_state["question_sent"] = False
        
        # コマンドボタンを再表示
        command_view = self.create_command_buttons()
        await ctx.send("正解をゲームチャンネルに送信しました。コマンドボタンを使用してください。", view=command_view, delete_after=5.0)
        
        # 元のメッセージを削除
        try:
            await ctx.message.delete()
        except:
            pass  # 削除できない場合は無視
        
        # コマンドボタンを更新
        await self.update_command_buttons(ctx.guild.id)
    
    async def handle_score_command(self, ctx):
        """/scoreコマンドの処理"""
        # コマンドサーバー権限チェック
        if not await self.check_guild_permission(ctx, self.command_guild_id, "コマンド"):
            return
        
        # コマンドチャンネル権限チェック
        if not await self.check_channel_permission(ctx, self.command_channel_id, "コマンド"):
            return
        
        # ゲームサーバーIDを取得
        game_guild_id = self.game_guild_id or ctx.guild.id
        
        if not self.game_manager.get_game_state(game_guild_id):
            await ctx.send("現在アクティブなゲームはありません。/start で開始してください。", delete_after=5.0)
            return
        
        game_state = self.game_manager.get_game_state(game_guild_id)
        sorted_scores = sorted(game_state["scores"].items(), key=lambda item: item[1], reverse=True)
        round_num = game_state["round"]
        
        if game_state.get("game_ended"):
            ranking_msg = "**--- 最終順位 ---**\n"
            rank = 1
            prev_score = -1
            for i, (user_id, score) in enumerate(sorted_scores):
                try:
                    user = await self.bot.fetch_user(user_id)
                    if score < prev_score:
                        rank = i + 1
                    ranking_msg += f"{rank}位: {user.display_name} ({score}点)\n"
                    prev_score = score
                except Exception:
                    ranking_msg += f"{rank}位: ユーザーID:{user_id} ({score}点)\n"
                    prev_score = score
            await self.send_to_game_channel(ctx, ranking_msg)
            await self.game_manager.log_score(game_guild_id, sorted_scores, ended=True, round_num=round_num)
            self.game_manager.end_game(game_guild_id)
            
            # コマンドボタンを再表示
            command_view = self.create_command_buttons()
            await ctx.send("最終結果をゲームチャンネルに送信しました。コマンドボタンを使用してください。", view=command_view, delete_after=5.0)
        else:
            scoreboard_msg = "**--- 現在のスコア ---**\n"
            for user_id, score in sorted_scores:
                try:
                    user = await self.bot.fetch_user(user_id)
                    scoreboard_msg += f"{user.display_name}: {score}点\n"
                except Exception:
                    scoreboard_msg += f"ユーザーID:{user_id}: {score}点\n"
            await self.send_to_game_channel(ctx, scoreboard_msg)
            await self.game_manager.log_score(game_guild_id, sorted_scores, ended=False, round_num=round_num)
            
            # コマンドボタンを再表示
            command_view = self.create_command_buttons()
            await ctx.send("現在のスコアをゲームチャンネルに送信しました。コマンドボタンを使用してください。", view=command_view, delete_after=5.0)
        
        # 元のメッセージを削除
        try:
            await ctx.message.delete()
        except:
            pass  # 削除できない場合は無視
        
        # コマンドボタンを更新
        await self.update_command_buttons(ctx.guild.id)
    
    async def update_command_buttons(self, guild_id):
        """コマンドチャンネルのボタンを更新"""
        if self.command_guild_id is None or self.command_channel_id is None:
            return
        
        try:
            command_guild = self.bot.get_guild(self.command_guild_id)
            if command_guild:
                command_channel = command_guild.get_channel(self.command_channel_id)
                if command_channel:
                    # 最新のメッセージを取得
                    async for message in command_channel.history(limit=10):
                        # ボタンが含まれているメッセージを探す
                        if message.components:
                            # ゲーム状態を判定
                            game_guild_id = self.game_guild_id or guild_id
                            is_question_active = self.game_manager.is_question_active(game_guild_id)
                            is_waiting_answer = self.game_manager.is_waiting_for_answer(game_guild_id)
                            is_game_active = self.game_manager.is_game_active(game_guild_id)
                            
                            if is_question_active:
                                # 問題出題中は全てのボタンを無効化
                                new_view = self.create_command_buttons_disabled()
                                await message.edit(content=message.content, view=new_view)
                            elif is_waiting_answer:
                                # 回答時間終了後で正解未発表の状態は出題ボタンも無効化
                                new_view = self.create_command_buttons_waiting_answer()
                                await message.edit(content=message.content, view=new_view)
                            elif is_game_active:
                                # ゲーム進行中は開始ボタンのみ無効化
                                new_view = self.create_command_buttons_game_active()
                                await message.edit(content=message.content, view=new_view)
                            else:
                                # ゲーム開始前は全てのボタンを有効化
                                new_view = self.create_command_buttons()
                                await message.edit(content=message.content, view=new_view)
                            break
        except Exception as e:
            print(f"コマンドボタン更新エラー: {e}") 