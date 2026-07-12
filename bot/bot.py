import re

import asyncio
import logging
import random
import time

import disnake
from disnake.ext import commands, tasks
from disnake import DMChannel

import os
import sys

from bot import storage
from bot.flag_system.flag_system import flags
from bot.utils import create_embed
from bot.storage import Messages, Channels, Guilds

os.environ["PYTHONIOENCODING"] = "UTF-8"

logger = logging.getLogger("robocat.bot")

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

@bot.event
async def on_ready():
    logger.info("Бот готов: залогинен как %s (id=%s)", bot.user, bot.user.id)
    channel = bot.get_channel(Channels.secret)
    if channel:
        await channel.send('Я тут!')
    else:
        logger.warning("Секретный канал %s не найден — стартовое сообщение не отправлено", Channels.secret)

@bot.event
async def on_error(event, *args, **kwargs):
    """Пишет необработанные исключения из listeners в bot.log (дефолт disnake печатает только в stderr)."""
    logger.exception("Необработанная ошибка в событии %s", event)

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
    if message.channel.id in Channels.news_reaction_channels:
        if message.author.bot or message.type not in (disnake.MessageType.default, disnake.MessageType.reply):
            return
        reactions = ['❤️', '👍', '👎']
        for reaction in reactions:
            await message.add_reaction(reaction)
            await asyncio.sleep(1)
        try:
            await message.create_thread(name="Обсуждение", reason="Тред на новую новость")
        except disnake.HTTPException:
            logger.warning("Не удалось создать тред под новостью %s в канале %s", message.id, message.channel.id)
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
    if inter.guild.id == Guilds.main:
        channel = bot.get_channel(Channels.welcome)
        if channel is None:
            logger.error("Канал приветствий %s не найден — отправка welcome для %s сейчас упадёт", Channels.welcome, inter.id)
        if await flags.getFlag(inter, "left"):
            await channel.send(random.choice(Messages.join_again).replace("%1", f"<@{inter.id}>"))
            await flags.removeFlag(inter,"left","Зашёл обратно")
        else:
            await channel.send(random.choice(Messages.join).replace("%1", f"<@{inter.id}>"))

@bot.listen()
async def on_raw_member_remove(payload: disnake.RawGuildMemberRemoveEvent):
    if payload.guild_id != Guilds.main:
        return
    if payload.user:
        await flags.setFlag(payload.user, "left", int(time.time()))


# #TODO: не работает?
# @bot.listen()
# async def on_message_delete(message: disnake.Message):
#     if message.channel in [1139036448201392218, 1215338737286914109, 1139036637519683584]:
#         backup_channel = message.guild.get_channel(1138425079483609220)
#         await backup_channel.send(f"Кто-то удалил сообщение в <@{message.channel.id}>")
#         await backup_channel.send(content=message.content)