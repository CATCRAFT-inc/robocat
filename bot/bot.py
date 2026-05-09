import re

import asyncio
import random

import disnake
from disnake.ext import commands, tasks
from disnake import DMChannel

import os
import sys

from bot import storage
from bot.flag_system.flag_system import Flags
from bot.utils import create_embed
from bot.storage import Messages

os.environ["PYTHONIOENCODING"] = "UTF-8"

intents = disnake.Intents.all()

bot = commands.Bot(
    intents=intents,
    activity=disnake.Game(name='Кошкокрафт'),
    command_prefix='!',
    reload=True,
    command_sync_flags=commands.CommandSyncFlags.all(),
    strip_after_prefix=True,
    test_guilds=[1138425078493753366],
    owner_id=531208170098655233
)
bot.flags = Flags()

@bot.event
async def on_ready():
    print("Я родился!")
    channel = bot.get_channel(1138425079483609220)
    await channel.send('Я тут!')

# TODO: Переписать, не работает
# @bot.slash_command()
# @commands.has_any_role(1188168267823595651)
# async def restart(inter: disnake.ApplicationCommandInteraction):
#     await inter.send('Скоро буду!')
#     await asyncio.sleep(3)
#     os.system('python3 main.py')
#     sys.exit(0)


@bot.listen()
async def on_message(message: disnake.Message):
    """
    Функция на добавление реакций и создания треда под каждым постом в заданных каналах
    + пишет, если игрок ошибся при написании Робокотику, а не Робокотику SRV
    """
    channels_to_check = [1139036448201392218, 1215338737286914109, 1139036637519683584]
    if message.channel.id in channels_to_check:
        reactions = ['❤️', '👍', '👎']
        for reaction in reactions:
            await message.add_reaction(reaction)
            await asyncio.sleep(1)
        await message.create_thread(name="Обсуждение", reason="Тред на новую новость")
    elif isinstance(message.channel, DMChannel):
        # Если сообщение — ровно 4 цифры
        if re.fullmatch(r'\d{4}', message.content):
            # Отправляем ответ
            await message.channel.send("Привет, я не тот **Робокотик**! Тебе нужен <@1149912580358422560> =)")

@bot.listen()
async def on_member_join(inter: disnake.Member):
    """
    Кидает случайное сообщение о входе юзера и выдаёт роли, если они были
    """
    if inter.guild.id == 1138425078493753366:
        channel = bot.get_channel(1138425079231938683) # TODO: убрать хардкод
        await channel.send(random.choice(Messages.join).replace("%1", f"<@{inter.id}>"))

# #TODO: не работает?
# @bot.listen()
# async def on_message_delete(message: disnake.Message):
#     if message.channel in [1139036448201392218, 1215338737286914109, 1139036637519683584]:
#         backup_channel = message.guild.get_channel(1138425079483609220)
#         await backup_channel.send(f"Кто-то удалил сообщение в <@{message.channel.id}>")
#         await backup_channel.send(content=message.content)

# @bot.listen()
# async def on_thread_create(thread: disnake.Thread):
#     """
#     Пишет сообщения в определенные треды
#     """
#     if thread.guild.id == 1138425078493753366:
#         forum = bot.get_channel(thread.parent_id)

#         try:
#             tag = forum.get_tag_by_name('на рассмотрении')
#             await thread.add_tags(tag)
#         except:
#             pass

#         if thread.parent_id == 1240040560019111987:  # Баги
#             await thread.send(embed=create_embed(
#                 title="Спасибо за репорт бага!",
#                 description="Пожалуйста убедись, что баг написан по шаблону (как закреплённый тред в этом форуме).\n"
#                             "Без описания или пути воспроизведения бага мы не сможем его пофиксить, а без твоего ника мы не сможем выдать тебе АРы!"
#             ))
#         elif thread.parent_id == 1143564055999676416:  # Запросы
#             await thread.send(embed=create_embed(
#                 title="По поводу Котячьих Заслуг",
#                 description="Если ты подал запрос на **Котячью Заслугу**, то указывай какая именно тебе нужна (строительство, идея, друзья) и прикрепи за что именно мы должны её тебе выдать!"
#             ))