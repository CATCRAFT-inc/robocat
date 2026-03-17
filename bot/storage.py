from dataclasses import dataclass

import disnake

from bot.utils import create_embed
from bot.utils import create_button
from .bot import commands

class EmojiStorage:
    diamond = disnake.PartialEmoji(name='diamond', id=1182123565491953704,animated=True)
    animated_diamond = disnake.PartialEmoji(name='diamond', id=1182123565491953704,
                                                                    animated=True)
    blistering = disnake.PartialEmoji(name='guiding', animated=True,id=1179100424930857022)
    pickaxe = disnake.PartialEmoji(name='kirka', id=1175696173919653919)
    ar = disnake.PartialEmoji(name='ar', id=1153483933091110912)
    rofl = disnake.PartialEmoji(name='mazzelov', id=1179100218717896784)
    ab = disnake.PartialEmoji(name='ab~1', id=1175696024292040814)
    book = disnake.PartialEmoji(name='written_book', id=1186823346155962460)

    kplus = disnake.PartialEmoji(name="kp", id=1280167088559882301)
    kplusplus = disnake.PartialEmoji(name="kpp", id=1280167090111647774)
    kplusplusplus = disnake.PartialEmoji(name="kppp", id=1280167091756077159)

class LinksStorage:
    mapLink = 'https://map.catcraftmc.ru'
    wikiLink = 'https://wiki.catcraftmc.ru'
    youtubeLink = 'https://www.youtube.com/@catcraftminecraft'
    vkLink = 'https://vk.com/catcraftmc'

class ColorStorage:
    main = "#4f2dbe"
    gray = "#808080"

class Channels:
    command_logs = 1237318531784249394
    discord_logs = 1260242017242448054
    secret = 1138425079483609220
    general = 1138425079231938686
    for_bots = 1171103961319751730
    support = 1145465696164266166

    app_category_1 = 1138607394079907900
    app_category_2 = 1222881180890960044
    app_category_3 = 1222881206094401556

    reports_category = 1145467946710339754
    admin_reports_category = 1185587487108775978

    about_server = 1138634937638060144
    write_app = 1138425079231938682

    ticket_log = 1260242017242448054
    nsfw = 1174669054938726410

    bugs = 1483429604248129558
    temp_bugs = 1481600048809640106
    ideas = 1141682290146148392
    requests = 1143564055999676416

# TODO: Сделать динамично

# class Users:
#     admins = {
#     "szarkan": 531208170098655233,
#     "bkke": 538309573774278666
#     }
#     moderators = {
#     "avokato_AMF": 827888128567672892,
#     "Mr_Milota": 392314414310883328,
#     "ZakharEren": 929316548865294376
#     }

class Roles:
    ## Admins|Главные
    owner = 1466840794190057574 # Основатель
    st_admin = 1138425078917369953
    admin = 1188168267823595651 # Всекот
    developer = 1466927118494466266 # Разработчик
    moderator =  1138425078917369952 # Всекотёнок, модератор
    call_admin = 1163542634191654962 # Позвать админа
    app_viewer = 1138639647086497842 # Смотрит заявки
    ## КСБ
    ksb = 1138425078493753368 # Офицер КСБ
    junior_ksb = 1380821070785019904 # Мл. Офицер КСБ
    st_ksb = 1380821280546226227 # Ст. Офицер КСБ
    gksb = 1141404357988995133 # ГКСБ
    ## MISC
    no_respect = 1221184379397738657 # Потеря благословления
    accepted = 1242389208669229137 # Не знаю
    no_apps = 1216910429737980065 # Запрет заявок
    parlament = 1141404858268782705 # Парламентёр
    player = 1242389208669229137 # Котик
    ## Notifications
    rp = 1139039308678963250
    events = 1139038901500133446
    maintanence = 1139039206090485860
    media = 1139039063127621762
    server_updates = 1154962203007533168
    site_updates = 1216088841761329252

class Users:
    szarkan = 531208170098655233

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
        f"> Привет, %1! Добро пожаловать на **Кошкокрафт**!\n- О нашем сервере в канале <#{Channels.about_server}>\n",
        f"> Котик %1 прибыл в Кошкокрафт!\n- О нашем сервере в канале <#{Channels.about_server}>\n",
        f"> К нам пришёл %1!\n- О нашем сервере в канале <#{Channels.about_server}>\n"
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

class Embeds:
    bug_report_embed = disnake.ui.Container(
        disnake.ui.TextDisplay("❗️ ## Нашёл баг?"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("**Чудесно!** Ну, *не очень*, но сообщи о нём нам кнопкой ниже и мы пофиксим его в кратчайшие сроки!"),
        disnake.ui.ActionRow(
            disnake.ui.Button(style=disnake.ButtonStyle.green, label="Сообщить о баге", custom_id=Buttons.BUG_REPORT.id)
        )
    )
    role_choose = disnake.ui.Container(
        disnake.ui.TextDisplay("# 📢 Роли уведомлений"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("**Эти роли отвечают за пинги от объявлений, которые хотите отслеживать.**\n**Для получения** - нажмите на пункт с ролью в списке ниже.\n**Для снятия роли** - нажмите повторно.**\n"),
        disnake.ui.TextDisplay("-# Если роль не выдалась, попробуйте нажать ещё раз!"),
        disnake.ui.ActionRow(
            disnake.ui.StringSelect(
                options=[
                    disnake.SelectOption(label="Обновления сервера", description="Уведомлять о новых геймплейных изменениях", value=Roles.server_updates),
                    disnake.SelectOption(label="Газета", description="Уведомлять о РП постах от игроков", value=Roles.rp),
                    disnake.SelectOption(label="Ивенты", description="Уведомлять об ивентах, розыгрышах, результатов и т.д.", value=Roles.events),
                    disnake.SelectOption(label="Медия", description="Уведомлять о стримах и видео", value=Roles.media),
                    disnake.SelectOption(label="Тех. работы", description="Уведомлять о предстоящих и оконченных тех. работах", value=Roles.maintanence),
                    disnake.SelectOption(label="Обновление сайта", description="Уведомлять об изменениях нашего сайта", value=Roles.site_updates),
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
    choose_help_ticket = disnake.ui.Container(
        disnake.ui.TextDisplay("## :exclamation: Получить помощь"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("\n### <:admin:1389370820274425917> Связь с админами\nОтправить жалобу админам на игроков или связаться с админами по разным вопросам"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("\n### <:ksb_chief:1389370827421651096> Жалоба в КСБ\nЖалоба в Кошачью Службу Безопасности, если кто-то нарушил закон и нужна справедливость"),
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