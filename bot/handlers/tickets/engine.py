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
        """/done - положительно закрывает баг-репорты, идеи, запросы и админ-тикеты

        Args:
            inter (disnake.ApplicationCommandInteraction): _description_
            comment (str, optional): Комментарий к сообщению закрытия. Defaults to None.
        """
        thread = inter.channel
        forum = thread.parent
        owner = thread.owner

        match forum.id:
            case Channels.ideas:
                tag_added = forum.get_tag_by_name('Добавлено')
                tag_rejected = forum.get_tag_by_name("Отклонено")

                if tag_rejected in thread.applied_tags:
                    await thread.remove_tags(tag_rejected)
                await thread.add_tags(tag_added)
                idea_embed = create_embed(
                    title="💫 Идея добавлена!",
                    description="Предложенная тобой идея была реализована на сервере!\n**Спасибо**💖",
                    color=disnake.Colour.yellow
                )
                if comment:
                    idea_embed.add_field(name="Комментарий", value=comment)
                await thread.send(f"{owner.mention if owner is not None else ""}", embed=idea_embed)
            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                if tag_rejected in thread.applied_tags:
                    await thread.remove_tags(tag_rejected)
                await thread.add_tags(tag_done)
                request_embed = create_embed(
                    title="💫 Запрос выполнен!",
                    color=disnake.Colour.yellow
                )
                if comment:
                    request_embed.add_field(name="Комментарий", value=comment)
                await thread.send(f"{owner.mention if owner is not None else ""}", embed=request_embed)
            case _:
                if isinstance(thread, disnake.Thread):
                    match forum.id:
                        case Channels.bugs:
                            await inter.response.defer()
                            description = "Огромное спасибо за репорт бага! Ты помогаешь делать сервер лучше 💖"
                            if comment:
                                description += f"\n**Комментарий:**\n{comment}"
                            bug_fixed_embed = create_container(
                                title="💫 Сообщённый тобой баг пофикшен!",
                                description=description
                            )
                            user_id = await Flags().getFlag(inter.channel,"created_by")
                            member = inter.guild.get_member(int(user_id.value)) if user_id else None
                            if member:
                                await member.send(components=bug_fixed_embed)
                            await inter.channel.delete(reason=f"Баг закрыт {inter.author.id}")
                            await Flags().removeFlag(inter.channel,"created_by")
                        case Channels.support:
                            if not comment:
                                await inter.send("Для админских тикетов нужно обязательно указать комментарий с итогом репорта или вынесеным решением!", ephemeral=True)
                                return
                            await inter.response.defer()
                            ticket_closed_embed = disnake.ui.Container(
                                disnake.ui.TextDisplay("## ⭐️ Твой админский тикет закрыт!"),
                                disnake.ui.Separator(),
                                disnake.ui.TextDisplay(f"### Решение:\n{comment}\n-# Закрыл: <@{inter.author.id}>")
                            )
                            user_id = await Flags().getFlag(inter.channel, "created_by")
                            member = inter.guild.get_member(int(user_id.value)) if user_id else None
                            if member:
                                await member.send(components=ticket_closed_embed)
                            await inter.channel.delete(reason=f"Тикет закрыт {inter.author.id}")
                            await Flags().removeFlag(inter.channel,"created_by")
                        case _:   
                            await inter.send("Команду можно прописывать только в тредах багов, идеей, тикетов или запросов!", ephemeral=True)


    @commands.slash_command(name='decline', description='Отклонить баг, идею, тикет')
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def declineCommand(self, inter: disnake.ApplicationCommandInteraction,
                        reason: str = None):
        """Команда /decline - отклонение тикетов админов, багов, идей или запросов

        Args:
            inter (disnake.ApplicationCommandInteraction): _description_
            reason (str, optional): Причина закрытия, если не указана - обычно возвращает "Не указана..." Defaults to None.
        """
        thread = inter.channel
        forum = thread.parent
        owner = thread.owner

        match forum.id:
            case Channels.ideas:
                tag_added = forum.get_tag_by_name('Добавлено')
                tag_rejected = forum.get_tag_by_name("Отклонено")

                await thread.remove_tags(tag_added, "/decline") 
                await thread.add_tags(tag_rejected, "/decline")
                idea_embed = create_embed(
                    title=" Идея отклонена...",
                    description="Большое спасибо за предложение идеи, но, к сожалению, она была отклонена...",
                    color=disnake.Colour.yellow
                )
                idea_embed.add_field(name="Причина:", value=reason or "Не указали...")
                await thread.send(f"{owner.mention if owner else ""}", embed=idea_embed)
            case Channels.requests:
                tag_done = forum.get_tag_by_name('Исполнено')
                tag_rejected = forum.get_tag_by_name("Отказано")
                await thread.remove_tags(tag_done)
                await thread.add_tags(tag_rejected)
                request_embed = create_embed(
                    title="😔 Запрос отклонён",
                    color=disnake.Colour.yellow
                )
                request_embed.add_field(name="Причина", value=reason or "Не указали...")
                await thread.send(f"{owner.mention if owner else ""}", embed=request_embed)
            case _:
                if isinstance(thread, disnake.Thread):
                    match forum.id:
                        case Channels.bugs:
                            await inter.response.defer()
                            if reason is None:
                                reason = "Не указали..."
                            bug_fixed_embed = create_container(
                                title = "😔 Сообщённый тобой баг отклонён...",
                                description = f"Огромное спасибо за репорт бага, но он был отклонён...\n**Причина:** {reason}"
                            )
                            user_id = await Flags().getFlag(inter.channel,"created_by")
                            member = inter.guild.get_member(user_id.value) if user_id else None
                            if member:
                                await member.send(components=bug_fixed_embed)
                            await inter.channel.delete(reason=f"Баг закрыт {inter.author.id}")
                            await Flags().removeFlag(inter.channel,"created_by")
                        case _:   
                            await inter.send("Команду можно прописывать только в тредах багов, идеей, тикетов или запросов!", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(TicketEngine(bot))