import disnake
from disnake.ext import commands

from bot.storage import Roles
from .flag_system import flags


def _format_flag_list(flag_list: list[tuple]) -> str:
    lines = []
    for flag_key, flag_value, flag_expires in flag_list:
        expires_str = f"<t:{flag_expires}:R>" if flag_expires else "Никогда"
        lines.append(f"**{flag_key}**\n`{flag_value}`\n**Истекает:** {expires_str}\n")
    return "\n".join(lines)


class FlagCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name='list_user_flags', description="Показать все флаги пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def allUserFlagsCommand(self, inter: disnake.ApplicationCommandInteraction,
                                  user: disnake.Member):
        flag_list = await flags.getAllFlags(user)
        if flag_list:
            await inter.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay(f"Флаги пользователя {user.mention}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(_format_flag_list(flag_list))
            ), allowed_mentions=disnake.AllowedMentions(users=False))
        else:
            await inter.send("У данного пользователя нет флагов.", ephemeral=True)

    @commands.slash_command(name='get_user_flag', description="Показать значение определённого флага пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def getUserFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                                 flag: str,
                                 user: disnake.Member):
        flag_info = await flags.getFlag(user, flag)
        if flag_info:
            expires_str = f"\nИстекает: <t:{flag_info.expires_at}:R>" if flag_info.expires_at else ""
            value_str = f"Значение: `{flag_info.value}`{expires_str}"
            await inter.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay(f"Флаг `{flag}` пользователя {user.mention}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(value_str)
            ), allowed_mentions=disnake.AllowedMentions(users=False))
        else:
            await inter.send("У данного пользователя нет такого флага.", ephemeral=True)

    @commands.slash_command(name='list_channel_flags', description="Показать все флаги канала")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def allChannelFlagsCommand(self, inter: disnake.ApplicationCommandInteraction,
                                     channel: disnake.abc.GuildChannel = None):
        if channel is None:
            channel = inter.channel
        flag_list = await flags.getAllFlags(channel)
        if flag_list:
            await inter.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay(f"Флаги канала {channel.mention}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(_format_flag_list(flag_list))
            ), allowed_mentions=disnake.AllowedMentions(users=False))
        else:
            await inter.send("У данного канала нет флагов.", ephemeral=True)

    @commands.slash_command(name='flag_user', description="Добавить флаг на пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def addUserFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                                 member: disnake.Member,
                                 flag: str,
                                 value: str = None,
                                 expires_at: str = None):
        await flags.setFlag(member, flag, value, expires_at)
        await inter.send(f"Флаг `{flag}` установлен для {member.mention}.", ephemeral=True,
                         allowed_mentions=disnake.AllowedMentions(users=False))

    @commands.slash_command(name='flag_channel', description="Добавить флаг на канал")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def addChannelFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                                    channel: disnake.abc.GuildChannel,
                                    flag: str,
                                    value: str = None,
                                    expires_at: str = None):
        await flags.setFlag(channel, flag, value, expires_at)
        await inter.send(f"Флаг `{flag}` установлен для {channel.mention}.", ephemeral=True,
                         allowed_mentions=disnake.AllowedMentions(users=False))

    @commands.slash_command(name='remove_flag_user', description="Удалить флаг у пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def removeUserFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                                    member: disnake.Member,
                                    flag: str):
        await flags.removeFlag(member, flag, "Команда remove_flag_user")
        await inter.send(f"Флаг `{flag}` удалён у {member.mention}.", ephemeral=True,
                         allowed_mentions=disnake.AllowedMentions(users=False))

    @commands.slash_command(name='remove_channel_flag', description="Удалить флаг у канала")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def removeChannelFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                                       channel: disnake.abc.GuildChannel,
                                       flag: str):
        await flags.removeFlag(channel, flag, "Команда remove_channel_flag")
        await inter.send(f"Флаг `{flag}` удалён у {channel.mention}.", ephemeral=True,
                         allowed_mentions=disnake.AllowedMentions(users=False))


def setup(bot: commands.Bot):
    bot.add_cog(FlagCommands(bot))
