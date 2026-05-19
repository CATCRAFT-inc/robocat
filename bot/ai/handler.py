import disnake
from disnake.ext import commands

from bot.storage import Channels, Roles
from bot.flag_system.flag_system import flags


class AIMessageHandler(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def robocatAI(self, message: disnake.Message):
        if message.author.bot:
            return
        if message.content.startswith("!"):
            return
        if message.channel.id in [Channels.for_bots, Channels.secret]: # Отслеживаем сообщения только в двух чатах - для ботов и для теста
            if self.bot.user.mentioned_in(message) or (message.reference and message.reference.resolved.author == self.bot.user): # Если робокотика пинганули или ответили ему на сообщение
                if self.ai_locked and message.author.id not in self.ai_locked_bypass_user_ids:
                    return "*Робокотик остужает свой процессор... Поговори с ним попозже.*"
                if await self._reachedLimit(message.author):
                    ai_locked_flag = await flags.getFlag(message.author, "ai_locked")
                    expires_at = ai_locked_flag.expires_at or None
                    if expires_at:
                        expires_at = f"<t:{expires_at}:R>"
                    else:
                        expires_at = "попозже"
                    await message.reply(f"К сожалению у тебя закончился лимит ежедневных запросов! Попробуй {expires_at}!\n-# Забусти сервер или стань **Котик+**, чтобы иметь неограниченные запросы!")
                    return
                if message.channel.id not in [Channels.for_bots, Channels.secret]:
                    await message.reply(f"*Общение с Робокотиком доступно только в <#{Channels.for_bots}>*", delete_after=5)

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
                
                conversation = await self._buildConverstaion(messages)

                async with message.channel.typing():
                    reply, attachment, thinking_message = await self.generateAnswer(conversation, message)
                    if thinking_message:
                        await thinking_message.delete()
                    if len(reply) > 1999:
                        answers = [reply[i:i+1999] for i in range(0, len(reply), 1999)]
                        for mes in answers:
                            await message.reply(mes)
                        if attachment:
                            await message.reply(file=attachment)
                    else:
                        await message.reply(reply, file=attachment)
                    await self._limiter(message.author)
    
    @commands.slash_command(name='aiinfo', description="посмотреть инфу о ии")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiInfo(self, inter: disnake.MessageCommandInteraction):
        await inter.send(f"{self.current_model}, {self.current_vendor}, {self.locked_models}", ephemeral=True)
        token_used = await flags.getFlag("abstract", "token_used")
        if token_used:
            await inter.send(f"Token used: {token_used.value}t", ephemeral=True)
    
    @commands.slash_command(name='ailock', description="посмотреть инфу о ии")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiLock(self, inter: disnake.MessageCommandInteraction):
        if self.ai_locked:
            self.ai_locked = False
            await inter.send("ИИ разблокирован", ephemeral=True)
        else:
            self.ai_locked = True
            await inter.send("ИИ заблокирован", ephemeral=True)

    @commands.slash_command(name="reloadai", description="перезапуск клиента и системного промпта")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiReload(self, inter: disnake.MessageCommandInteraction):
        await self.client.close()
        await self._getNewClient()
        await self._loadAIData()

def setup(bot: commands.Bot):
    bot.add_cog(AIMessageHandler(bot))