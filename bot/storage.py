from dataclasses import dataclass

import disnake

from bot.discord_config import Channels, Emojis, Roles
from bot.utils import create_embed
from bot.utils import create_button
from .bot import commands

class LinksStorage:
    mapLink = 'https://map.catcraftmc.ru'
    wikiLink = 'https://wiki.catcraftmc.ru'
    youtubeLink = 'https://www.youtube.com/@catcraftminecraft'
    vkLink = 'https://vk.com/catcraftmc'

class ColorStorage:
    main = "#4f2dbe"
    gray = "#808080"

class Emotes:
    kiss = [
        "Тебя {} чмокнул(а)!",
        "Тебя {} поцеловал(а)!",
        "{} тебя жоско чмокнул(а)!",
        "{} тебя очень сильно поцеловал(а)!",
        "{} зачмокал(а) тебя!",
        "Тебя {} зачмокал(а)!"
    ]
    fuck = [
        '{} тебя трахнул(а)~',
        '{} решил(а) трахнуть тебя~',
        '{} оттрахал(а) тебя~',
        '{} трахнул(а) тебя~',
        '{} взял(а) инциативу в свои руки и оттрахал(а) тебя~'
    ]
    gycha = (
    'ГЫЧА', "МАФОН", "ДУПЛИЩЕ", "ГНОМ", "СЕРВЕР В МАРТЕ", "КЛЮВ", "М АФФООН ", "ГЫ ЧААААА", "НАМАФОНИЛИ ГНОМОВ",
    "КЛЮ ВВ", "мафон", "гыча", 'аспир гном', "гном аспир", "АСПИР ГНМО")

class Messages:
    join = [
        "> Привет, %1! Добро пожаловать на **Кошкокрафт**!\n- Информация о сервере - <#%2>\n",
        "> Котик %1 прибыл в Кошкокрафт!\n- Информация о сервере - <#%2>\n",
        "> К нам пришёл %1!\n- Информация о сервере - <#%2>\n",
        "> %1 присоединился к Кошкокрафту!\n- Информация о сервере - <#%2>\n",
        "> Встречайте нового котика %1!\n- Информация о сервере - <#%2>\n",
        "> %1 запрыгнул(а) на сервер!\n- Информация о сервере - <#%2>\n",
        "> Урааа, у нас пополнение — %1!\n- Информация о сервере - <#%2>\n"
    ]
    join_again = [
        f"> С возвращением, %1!",
        f"> %1 вернулся на Кошкокрафт!",
        f"> И снова привет, %1!"
    ]
    leave = [
        f"> Котик %1 покинул нас...\nТеперь нас %2",
        f"> Пока, %1...\nТеперь нас %2",
        f"> %1 вышел...\nТеперь нас %2"
    ]

random_stuff = {
    "kira_app": "Меня зовут Кира Йошикагэ. Мне 33 года. Мой дом находится в северо-восточной части Морио, в районе поместий. Работаю в офисе сети магазинов Kame Yu и домой возвращаюсь, самое позднее, в восемь вечера. Не курю, выпиваю изредка. Ложусь спать в 11 вечера и убеждаюсь, что получаю ровно восемь часов сна, несмотря ни на что. Перед сном я пью тёплое молоко, а также минут двадцать уделяю разминке, поэтому до утра сплю без особых проблем. Утром я просыпаюсь, не чувствуя ни усталости, ни стресса, словно младенец. На медосмотре мне сказали, что никаких проблем нет. Я пытаюсь донести, что я обычный человек, который хочет жить спокойной жизнью. Я не забиваю себе голову проблемами вроде побед или поражений, и не обзавожусь врагами, из-за которых не мог бы уснуть. Я знаю наверняка: в таком способе взаимодействия с обществом и кроется счастье. Хотя, если бы мне пришлось сражаться, я бы никому не проиграл. Жопа."
}

@dataclass
class ButtonData:
    id: str
    component: object


class Buttons:
    BUG_REPORT = ButtonData("bug_report", create_button(label="Сообщить о баге", custom_id="bug_report", style=disnake.ButtonStyle.danger))
    GET_A_JOB = ButtonData("show_vacansies", create_button(label="Посмотреть доступные вакансии", custom_id="show_vacansies", style=disnake.ButtonStyle.green))

class Embeds:
    bug_report_embed = disnake.ui.Container(
        disnake.ui.TextDisplay("## ❗️ Нашёл баг?"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("**Чудесно!** Ну, *не очень*, но сообщи о нём нам кнопкой ниже и мы пофиксим его в кратчайшие сроки!"),
        disnake.ui.ActionRow(
            disnake.ui.Button(style=disnake.ButtonStyle.green, label="Сообщить о баге", custom_id=Buttons.BUG_REPORT.id)
        )
    )
    @staticmethod
    def role_choose():
        return disnake.ui.Container(
            disnake.ui.TextDisplay("# 📢 Роли уведомлений"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("**Эти роли отвечают за пинги от объявлений, которые хотите отслеживать.**\n**Для получения** - нажмите на пункт с ролью в списке ниже.\n**Для снятия роли** - нажмите повторно.**\n"),
            disnake.ui.TextDisplay("-# Если роль не выдалась, попробуйте нажать ещё раз!"),
            disnake.ui.ActionRow(
                disnake.ui.StringSelect(
                    options=[
                        disnake.SelectOption(label="Обновления сервера", description="Уведомлять о новых геймплейных изменениях", value=str(Roles.server_updates)),
                        disnake.SelectOption(label="Газета", description="Уведомлять о РП постах от игроков", value=str(Roles.rp)),
                        disnake.SelectOption(label="Ивенты", description="Уведомлять об ивентах, розыгрышах, результатов и т.д.", value=str(Roles.events)),
                        disnake.SelectOption(label="Медия", description="Уведомлять о стримах и видео", value=str(Roles.media)),
                        disnake.SelectOption(label="Тех. работы", description="Уведомлять о предстоящих и оконченных тех. работах", value=str(Roles.maintanence)),
                        disnake.SelectOption(label="Обновление сайта", description="Уведомлять об изменениях нашего сайта", value=str(Roles.site_updates)),
                    ],
                    custom_id="ROLESELECT"
                )
            ),
            accent_colour=disnake.Color.from_hex(ColorStorage.main)
        )
    new_idea_template = disnake.ui.Container(
        disnake.ui.TextDisplay("## Шаблон и правила новой идеи"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("### Шаблон"),
        disnake.ui.TextDisplay("1. Твой ник\n2. Твоя идея\n3. (опционально) Почему ты считаешь что эта идея подходит серверу?"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("### Правила публикации"),
        disnake.ui.TextDisplay("1. Идея не должна повторять точь в точь идею другого сервера\n  - Но идея может повторять игры, настолки (аля ДнД) и подобное\n2. Если идея уже была в разработке до твоей подачи, то, к сожалению, она будет отклонена\n3. Автором идеи будет считаться игрок, подавший её первой"),
        accent_colour=disnake.Color.from_hex(ColorStorage.main)
    )
    @staticmethod
    def choose_help_ticket():
        return disnake.ui.Container(
            disnake.ui.TextDisplay("## :exclamation: Получить помощь"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"\n### <:admin:{Emojis.admin}> Связь с админами\nОтправить жалобу админам на игроков или связаться с админами по разным вопросам"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"\n### <:ksb_chief:{Emojis.ksb_chief}> Жалоба в КСБ\nЖалоба в Кошачью Службу Безопасности, если кто-то нарушил закон и нужна справедливость"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"\n### :broken_heart:  Баг-репорт\nЕсли нашёл баг, но правильное место для этого - <#{Channels.bugs}>"),
            disnake.ui.ActionRow(
                disnake.ui.StringSelect(
                    options=[
                        disnake.SelectOption(label="Связь с админами", description="Жалоба на игроков или в целом", value="TICKET_ADMIN"),
                        disnake.SelectOption(label="Жалоба в КСБ", description="Жалоба на нарушение законов", value="TICKET_POLICE"),
                        disnake.SelectOption(label="Баг-репорт", description="Заявить о баге", value="TICKET_BUGREPORT")
                    ],
                    custom_id="CHOOSE_TICKET"
                )
            )
        )





class FAQStorage:
    getsockopt = '''
Попробуй зайти по другому IP или убедись, что ты написал(а) его правильно:
```ansi
[2;36m[2;41m[2;45m[2;37mplay.catcraftmc.ru[0m[2;36m[2;45m[0m[2;36m[2;41m[0m[2;36m[0m - основной
[0;34mplay.catcraft.ru[0m[0m[2;45m[2;45m[0m[2;45m[0m - запасной
```
### **Не помогло?**
Попробуй проследовать [вот этому гайду](<https://wiki.bisquit.host/getsockopt>)
### **И это не помогло?**  
- Попробуй через второй IP  
- Попробуй переустановить Java  
- Попробуй на другом лаунчере  
- Попробуй с VPN
    '''

def setup(bot: commands.Bot):
    pass
