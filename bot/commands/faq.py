import disnake
from disnake.ext import commands

from bot.storage import Channels, FAQStorage, Roles


class FAQ(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def send_faq(self, ctx, embed):
        if ctx.message.reference:
            try:
                ref_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except (disnake.NotFound, disnake.HTTPException):
                await ctx.channel.send(components=embed)
            else:
                await ref_message.reply(components=embed)
        else:
            await ctx.channel.send(components=embed)

    @commands.command(name='немогузайти', aliases=['проблемысовходом'], description='Частые причины проблем со входом')
    async def getSockOptFAQ(self, ctx: commands.Context):
        embed = disnake.ui.Container(
                    disnake.ui.TextDisplay("## `getsockopt`"),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay(FAQStorage.getsockopt)
                )
        await self.send_faq(ctx,embed)

    # @commands.command(name='версия', aliases=['версиясервера'], description='Какая версия сервера?')
    # async def version_faq(self, ctx: commands.Context):
    #     embed = create_embed(
    #         title='Какая версия сервера?',
    #         description='Версия Кошкокрафта - `1.21.1 Java Edition`. Играть на Bedrock/PE нельзя.'
    #     )
    #     await self.send_faq(ctx,embed)

    @commands.command(name='айпи', aliases=["ip"], description='Показать IP сервера')
    async def ip_faq(self, ctx: commands.Context):
        embed = disnake.ui.Container(
            disnake.ui.TextDisplay("## Наши IP"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
"""
```ansi
[2;36m[2;41m[2;45m[2;37mplay.catcraft.ru[0m[2;36m[2;45m[0m[2;36m[2;41m[0m[2;36m[0m - основной
[0;34mplay.catcraftmc.ru[0m[0m[2;45m[2;45m[0m[2;45m[0m - запасной
```
                """
                
            )
        )
        await self.send_faq(ctx,embed)

    @commands.command(name='когдавайп', description="Информация по вайпу", aliases=['вайп'])
    async def whenWipe_faq(self, ctx: commands.Context):
        embed = disnake.ui.Container(
            disnake.ui.TextDisplay("## Когда вайп?"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("Как и всегда, новый сезон запланирован на период после экзаменов, ЕГЭ, ОГЭ и т.п. - начало июля.")
        )
        await self.send_faq(ctx,embed)

    @commands.command(name='гайд', description='Гайд для новичков', aliases=['guide'])
    async def guide(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### Гайд для новых игроков - <https://wiki.catcraft.ru/info/guide>")
        ))

    @commands.command(name='донатик', description='Донат нашего проекта!^~^', aliases=['донат', 'donate', "котик+"])
    async def donate(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### Донатик сервера - <https://donate.catcraftmc.ru>")
        ))

    @commands.command(name='законы', description='Законы сервера', aliases=['закон', 'laws', 'pfrjys'])
    async def laws(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### Законы Кошкоземья - <https://wiki.catcraftmc.ru/info/rules/laws/>"),
            disnake.ui.TextDisplay("-# Это законы мира, проще говоря - РП правила")
        ))

    @commands.command(name='правила', description='Правила сервера', aliases=['заповеди', 'rules', 'ghfdbkf'])
    async def rules(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### Заповеди Кошкоземья - <https://wiki.catcraftmc.ru/info/rules/rules/>"),
            disnake.ui.TextDisplay("-# Это правила сервера, за ними смотрят Всекоты")
        ))

    @commands.command(name='лаунчер', description='Информация про лаунчеры', aliases=['лаунчеры', 'какойлаунчервыбрать'])
    async def launcher(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### Помощь с выбором нормального лаунчера - <https://wiki.catcraft.ru/guides/other/launcher>"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("Кратко:\nПиратка - TLegacy\nЛицензия - Modrinth или MultiMC")
        ))

    @commands.command(name='история', description='История Кошкокрафта', aliases=['history'])
    async def history(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### История с самого зарождения Кошкокрафта до текущих дней - <https://wiki.catcraft.ru/history/1season/1season>")
        ))

    @commands.command(name='моды', description="Информация по модам для сервера")
    async def mods_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay("### Все нужные моды для наиболее комфортной игры - <https://wiki.catcraft.ru/info/mods>")
        ))

    # @commands.command(name='ник', description='Как поставить разноцветный ник', aliases=['nickname', 'nick', 'разноцветныйник'])
    # async def nick(self, ctx: commands.Context):
    #     await send_faq(ctx, embed=create_embed(
    #         title='У тебя Котик+ и ты хочешь красивый ник?',
    #         description='<https://wiki.catcraftmc.ru/guides/gameplay/rgb_nick>'
    #     ))

    @commands.command(name='запретныемоды', description='Запрещённые моды', aliases=['запрещено', 'запрещённыемоды', 'запрещенныемоды'])
    async def badmods(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay(
                '## Запрещённые моды на Кошкокрафте\n'
            ),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                'Они указаны на [этой странице](<https://wiki.catcraftmc.ru/info/rules/rules#%D0%B7%D0%B0%D0%BF%D0%BE%D0%B2%D0%B5%D0%B4%D0%B8-%D0%B6%D0%B8%D0%B7%D0%BD%D0%B8>), проверяй актуальный список там.\n'
                '\n'
                '- Любой вид мини-карт\n'
                '- Любой вид авто-кликеров\n'
                '- Любой вид показа здоровья и прочих аттрибутов других игроков\n'
                '- Модификации, выполняющие действия за игрока (пример: Baritone, Авто-тотем)\n'
                '  - Режим "принтера" в моде LiteMatica разрешён\n'
                '- FreeCam\n'
                '  - Разрешён только Freecam: Modrinth Edition — он не позволяет летать сквозь стены\n'
                '  - Использовать ReplayMod как FreeCam тоже запрещается\n'
                '- Чит-модификации (любого вида) и то, что в них входит\n'
                '- Чит-клиенты (любого вида) и то, что в них входит\n'
                '- SeedCracker (а также другие моды и программы для "взламывания" сида мира)\n'
                '- X-ray модификации и ресурспаки (любого вида)'
            )
            ))

    @commands.command(name='карта', aliases=['map', 'онлайнкарта', 'rfhnf'], description="Ссылка на онлайн-карту")
    async def map_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('### Онлайн-карта сервера\n<https://map.catcraftmc.ru>')
        ))

    @commands.command(name='вики', aliases=['wiki', 'dbrb'], description="Ссылка на Вики")
    async def wiki_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('### Вики сервера - <https://wiki.catcraft.ru>')
        ))

    @commands.command(name='тут', description="Проверить тут ли бот", aliases=["тут?"])
    async def ping_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('### Я тут =)')
        ))

    @commands.command(name='помощь', description="Информация по помощи от админов.", aliases=["нужнапомощь", "helpme"])
    async def needhelp_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay(
                '### Нужна помощь?'
            ),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                f'Напиши тикет админам - сразу же ответим: <#{Channels.support}>\n'
            )
        ))

    @commands.command(name='репорты', aliases=['ксб', 'репорт'], description="Информация по репортам КСБ")
    async def report_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('## Тебя ограбили/убили/обворовали?'),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f'Заходи в канал <#{Channels.support}> и отправляй жалобу на нарушителя!')
        ))

    # @commands.command(name='заявка', aliases=['попастьнасервер', 'написатьзаявку'], description="Информация о попадании на сервер")
    # async def app_faq(self, ctx: commands.Context):
    #     embed = create_embed(
    #         title='Как попасть на сервер?',
    #         description = "1. Переходишь в <#1138425079231938682>\n"
    #                       "2. Читаешь информацию о том, как написать **хорошую** заявку\n"
    #                       "3. Нажимаешь **Заполнить заявку**\n"
    #                       "4. Заполняешь все поля\n"
    #                       "5. Ждёшь принятия своей заявки\n"
    #                       "6. ???\n"
    #                       "7. Вот ты и на Кошкокрафте!\n"
    #     )
    #     await self.send_faq(ctx,embed)

    @commands.command(name='читайвики', aliases=['readwiki'], description="Прочитай Вики!")
    async def readwiki_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('### Пожалуйста, прочитай Вики!'),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                'Ответ на твой вопрос есть [на нашей Вики](<https://wiki.catcraftmc.ru>)!\n'
                'Лучше ведь потратить 5 минут на прочтение, чем ждать 5 минут **каждый раз** в ожидании вопроса.'
            )
        ))

    @commands.command(name='игровымпутём', description="Ищи игровым путём!", aliases=['игровымпутем'])
    async def gameway_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('### Ищи игровым путём!'),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                'Данный вопрос можно лишь найти самостоятельно! Админы помощи не дадут =)\n'
                'Попробуй найти на [Вики](<https://wiki.catcraftmc.ru>), '
                'поспрашивать игроков или подумать сильнее >3<'
            )
        ))

    @commands.command(name='да', description='да', aliases=['yes'])
    async def yes(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('## Да'),
        ))

    @commands.command(name='нет', description='нет', aliases=['no'])
    async def no(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('## Нет'),
        ))

    @commands.command(name='nometa', aliases=['безметы', 'номета'], description='Задавай вопрос сразу!')
    async def nometa_faq(self, ctx: commands.Context):
        await self.send_faq(ctx,embed=disnake.ui.Container(
            disnake.ui.TextDisplay('### Не задавай мета-вопросы!'),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(
                'Мета-вопрос — это вопрос, который подразумевает другие вопросы, например:\n'
                '- Можно задать вопрос?\n'
                '- Есть, кто разбирается в `N`?\n'
                '- [И подобное](<https://nometa.xyz/ru.html>)\n'
                'Лучше сразу задай вопрос целиком - сэкономишь время всем!'
            )
        ))

    @commands.Cog.listener(name='on_message')
    async def findFAQInMessages(self, message: disnake.Message):
        # Не отвечаем на ботов и на сообщения, где упомянут бот (иначе двойной ответ с ИИ)
        if message.author.bot or self.bot.user in message.mentions:
            return
        if message.author != self.bot.user:
            if 'когда вайп' in message.content.lower():
                await message.reply(components=disnake.ui.Container(
            disnake.ui.TextDisplay("## Когда вайп?"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("Как и всегда, новый сезон запланирован на период после экзаменов, ЕГЭ, ОГЭ и т.п. - начало июля.")
        ))
            elif 'где сборк' in message.content.lower():
                await message.reply(components=disnake.ui.Container(
            disnake.ui.TextDisplay("### Все нужные моды для наиболее комфортной игры - <https://wiki.catcraft.ru/info/mods>")
        ))

def setup(bot: commands.Bot):
    bot.add_cog(FAQ(bot))