from email.mime import message
import re

import disnake
from disnake.ext import commands

from bot.storage import Channels, Roles
from bot.flag_system.flag_system import flags

from .engine import AIEngine,  Status, FinalAnswer, AIError


class AIMessageHandler(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ai_engine = AIEngine()

        self.user_request_limit: int = 35

    async def cog_load(self):
        await self.ai_engine.load_ai(self.bot)

    async def _reachedLimit(self, user: disnake.User):
        """Ограничен ли юзер по лимиту запросов нейросети

        Args:
            user (disnake.User): _description_
        """
        # User - 35 RPD
        # Admins, K+, Boosters - inf RPD
        if Roles.premium_ai & {r.id for r in user.roles}:
            return False
        is_locked = await flags.getFlag(user, "ai_locked")
        if is_locked:
            return True
        return False

    async def _limiter(self, user: disnake.User):
        if Roles.premium_ai & {r.id for r in user.roles}:
            return
        current_req = await flags.getFlag(user, "airequests")
        print(current_req)
        if current_req is None:
            await flags.setFlag(user, "airequests", 1, expires_at="8ч")
        else:
            await flags.setFlag(user, "airequests", "+1")
            if int(current_req.value) + 1 >= self.user_request_limit:
                await flags.setFlag(user, "ai_locked", None, "8ч")

    async def _buildLongMessage(self, text: str) -> list[str]:
        chunks = []

        while len(text) > 3990:
            chunk = text[:3990]
            lang = self._getCodeBlockLang(chunk)

            if lang is not None:
                chunk += '```'
                text = f'```{lang}\n' + text[3990:]
            else:
                text = text[3990:]

            chunks.append(chunk)

        chunks.append(text)
        return chunks

    @staticmethod
    def _getCodeBlockLang(chunk: str) -> str | None:
        in_block = False
        lang = ''
        i = 0

        while i < len(chunk):
            if chunk[i:i+3] == '```':
                if not in_block:
                    match = re.match(r'(\w*)', chunk[i+3:])
                    lang = match.group(1) if match else ''
                    in_block = True
                else:
                    in_block = False
                    lang = ''
                i += 3
            else:
                i += 1

        return lang if in_block else None

    @commands.Cog.listener("on_message")
    async def robocatAI(self, message: disnake.Message):
        if message.author.bot:
            return
        if message.content.startswith("!"):
            return
        allowed_channels = {Channels.for_bots, Channels.secret}
        parent_id = getattr(message.channel, 'parent_id', None)

        #if message.channel.id in allowed_channels or parent_id == Channels.for_bots:
        if self.bot.user.mentioned_in(message) or (message.reference and message.reference.resolved.author == self.bot.user): # Если робокотика пинганули или ответили ему на сообщение
            if self.ai_engine.ai_locked and message.author.id not in self.ai_engine.ai_locked_bypass_user_ids:
                await message.reply("*Робокотик остужает свой процессор... Поговори с ним попозже.*") 
                return
            if await self._reachedLimit(message.author):
                ai_locked_flag = await flags.getFlag(message.author, "ai_locked")
                expires_at = ai_locked_flag.expires_at or None
                if expires_at:
                    expires_at = f"<t:{expires_at}:R>"
                else:
                    expires_at = "попозже"
                await message.reply(f"К сожалению у тебя закончился лимит ежедневных запросов! Попробуй {expires_at}!\n-# Забусти сервер или стань **Котик+**, чтобы иметь неограниченные запросы!")
                return

            messages = [message]
            current_msg = message
            
            while len(messages) < 5 and current_msg.reference:
                try:
                    prev_msg = current_msg.reference.resolved
                    if prev_msg is None:
                        prev_msg = await message.channel.fetch_message(current_msg.reference.message_id)
                    
                    messages.insert(0, prev_msg)
                    
                    current_msg = prev_msg
                except disnake.NotFound:
                    break
            
            conversation = await self.ai_engine.buildConverstaion(messages)


            async with message.channel.typing():
                thinking_message = None
                async for event in self.ai_engine.generateAnswer(conversation, message.author):
                    if isinstance(event, FinalAnswer):
                        if len(event.content) > 1999:
                            chunks = await self._buildLongMessage(event.content)
                            if thinking_message:
                                await thinking_message.delete()
                            for mes in chunks:
                                await message.reply(content="-# cut",components=disnake.ui.Container(
                                    disnake.ui.TextDisplay(mes)
                                ))
                            if event.attachments:
                                await message.reply(files=event.attachments)
                        else:
                            if thinking_message:
                                if event.attachments:
                                    await thinking_message.edit(event.content, files=event.attachments)
                                else:
                                    await thinking_message.edit(event.content)
                            else:
                                if event.attachments:
                                    await message.reply(event.content, files=event.attachments)
                                else:
                                    await message.reply(event.content)
                    elif isinstance(event, Status):
                        if thinking_message:
                            await thinking_message.edit(event.content)
                        else:
                            thinking_message = await message.reply(event.content)
                            
                    elif isinstance(event, AIError):
                        if thinking_message:
                            await thinking_message.edit(event.content)
                        else:
                            thinking_message = await message.reply(event.content)
                        return
                await self._limiter(message.author)

    @commands.slash_command(name='aichat', description="Создать приватный чат с нейросетью")
    @commands.has_any_role(Roles.admin, Roles.st_admin, Roles.booster, Roles.kotikplus)
    async def aiChat(self, inter: disnake.MessageCommandInteraction):
        channel = inter.guild.get_channel(Channels.for_bots)
        thread = await channel.create_thread(
                name=f'ии-{inter.author.display_name}',
                type=disnake.ChannelType.private_thread,
                auto_archive_duration=10080,
                reason=f"Приватный чат с ии от {inter.author.display_name}"
            )
        await thread.send(components=disnake.ui.Container(
            disnake.ui.TextDisplay("# Приватный чат"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("Этот тред предназначен для приватного общения с нейросетью. Ответы нейросети могут быть ошибочны - перепроверяй информацию самостоятельно! Мы не ответственны за ответы нейросети."),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("Для начала общения, ответь на моё сообщение ниже! (**не на это :3**)"),
            disnake.ui.TextDisplay(f"||-# {inter.author.mention} <@{531208170098655233}>||"),
            )
        )
        await thread.send("Привет! Ответь на это сообщение, чтобы начать чат!")
        await inter.send(f"Приватный тред создан - <#{thread.id}>", ephemeral=True)
    
    @commands.slash_command(name='aiinfo', description="посмотреть инфу о ии")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiInfo(self, inter: disnake.MessageCommandInteraction):
        await inter.send(f"{self.ai_engine.current_model}, {self.ai_engine.current_vendor}, {self.ai_engine.locked_models}", ephemeral=True)
        token_used = await flags.getFlag("abstract", "token_used")
        if token_used:
            await inter.send(f"Token used: {token_used.value}t", ephemeral=True)
    
    @commands.slash_command(name='ailock', description="посмотреть инфу о ии")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiLock(self, inter: disnake.MessageCommandInteraction):
        if self.ai_engine.ai_locked:
            self.ai_engine.ai_locked = False
            await inter.send("ИИ разблокирован", ephemeral=True)
        else:
            self.ai_engine.ai_locked = True
            await inter.send("ИИ заблокирован", ephemeral=True)

    @commands.slash_command(name="reloadai", description="перезапуск клиента и системного промпта")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiReload(self, inter: disnake.MessageCommandInteraction):
        await self.ai_engine.client.close()
        await self.ai_engine._getNewClient()
        await self.ai_engine._loadAIData()

def setup(bot: commands.Bot):
    bot.add_cog(AIMessageHandler(bot))