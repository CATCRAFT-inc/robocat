import disnake
from disnake.ext import commands

from bot.flag_system.flag_system import Flags
from bot.storage import Channels, ColorStorage, Roles
from .admin_ticket import AdminTicket
from bot.handlers.tickets.bugs import BugHandler
from bot.utils import create_container, create_embed


class TicketEngine(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_dropdown")
    async def chooseTicket(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id == "CHOOSE_TICKET":
            
            match inter.resolved_values[0]:
                case "TICKET_ADMIN":
                    await inter.response.send_modal(AdminTicket.AdminTicketModal())
                case "TICKET_POLICE":
                    await inter.send(components=create_container("## КСБ пока что нет!", "Сезон-то не начался, хех!"), ephemeral=True)
                case "TICKET_BUGREPORT":
                    await inter.response.send_modal(BugHandler.BugModal())
                case _:
                    await inter.send("Ой-ёй! Бот не нашёл такой тип тикета... Сообщи пожалуйста в **баг-репорт**!", ephemeral=True)
        # match inter.component.custom_id:
        #     case "TICKET_ADMIN":
        #         return
        #     case "TICKET_POLICE":
        #         return
        #     case _:
        #         pass

    @commands.slash_command(name='done', description='Фикс бага/добавление идеи/выполнение запроса')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def doneCommand(self, inter: disnake.ApplicationCommandInteraction,
                        comment: str = None):
        """Команда /done для закрытия багов, запросов, админ-тикетов и КСБ-репортов?

        Args:
            inter (disnake.ApplicationCommandInteraction):
            comment (str, optional): Комментарий, который отправить в эмбед. Defaults to None.
        """
        thread = inter.channel
        forum = thread.parent
        try:
            owner = thread.owner
        except:
            owner = None

        match forum.id:
            case Channels.ideas:
                tag_added = forum.get_tag_by_name('Добавлено')
                tag_rejected = forum.get_tag_by_name("Отклонено")

                if tag_rejected in thread.applied_tags:
                    await thread.remove_tags(tag_rejected)
                await thread.add_tags(tag_added)
                idea_added_embed = create_embed(
                    title="💫 Идея добавлена!",
                    description="Предложенная тобой идея была реализована на сервере!\n**Спасибо**💖",
                    color=disnake.Colour.yellow
                )
                if comment:
                    idea_added_embed.add_field(name="Комментарий", value=comment)
                await thread.send(f"{owner.mention if owner is not None else ""}", embed=idea_added_embed)
            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                if tag_rejected in thread.applied_tags:
                    await thread.remove_tags(tag_rejected)
                await thread.add_tags(tag_done)
                request_done_embed = create_embed(
                    title="💫 Запрос выполнен!",
                    color=disnake.Colour.yellow
                )
                if comment:
                    request_done_embed.add_field(name="Комментарий", value=comment)
                await thread.send(f"{owner.mention if owner is not None else ""}", embed=request_done_embed)
            case _:
                if isinstance(inter.channel, disnake.Thread):
                    match inter.channel.parent_id:
                        case Channels.bugs:
                            bug_fixed_embed = create_container(
                                "💫 Сообщённый тобой баг пофикшен!",
                                "Огромное спасибо за репорт бага! Ты помогаешь делать сервер лучше 💖"
                            )
                            if comment:
                                bug_fixed_embed.add_field(name="Комментарий", value=comment)
                            user_id = await Flags().getFlag(inter.channel,"created_by")
                            member = await inter.guild.get_member(user_id[0]) if user_id else None
                            if member:
                                await member.send(embed=bug_fixed_embed)
                            await inter.channel.delete(reason=f"Баг закрыт {inter.author.id}")
                            
                        case Channels.support:
                            if not comment:
                                await inter.send("Для админских тикетов нужно обязательно указать комментарий с итогом репорта или вынесеным решением!", ephemeral=True)
                                return
                            # TODO: Сначала сделать логику багов
                            ticket_closed_embed = disnake.ui.Container(
                                disnake.ui.TextDisplay("## 💫 Твой админский тикет закрыт!"),
                                disnake.ui.Separator(),
                                disnake.ui.TextDisplay(f"### Решение:\n{comment}\n-# Закрыл: <@{inter.author.id}>")
                            )
                            user_id = await Flags().getFlag(inter.channel, "created_by")
                            member = inter.guild.get_member(int(user_id[0]))
                            if member:
                                await member.send(components=ticket_closed_embed)
                            await inter.channel.delete(reason=f"Тикет закрыт {inter.author.id}")
                            await Flags().removeFlag(inter.channel,"created_by")
                        case _:   
                            await inter.send("Команду можно прописывать только в тредах багов, идеей, тикетов или запросов!", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(TicketEngine(bot))