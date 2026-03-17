import disnake
from disnake.ext import commands

from bot.storage import Roles

from .flag_system import Flags

class FlagCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.flags = Flags()

    @commands.slash_command(name='raw_list_flags', description="Сырой вызов - показать все флаги определенного типа и определенного айди")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def allFlagsCommand(self, inter: disnake.ApplicationCommandInteraction,
                            entity_type,
                            entity_id: str):
        flagList = await self.flags.listAllFlags(entity_type,int(entity_id))
        if flagList is not None:
            await inter.send(flagList)
    
    @commands.slash_command(name='list_user_flags', description="Показать все флаги пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def allUserFlagsCommand(self, inter: disnake.ApplicationCommandInteraction,
                            user: disnake.Member):
        flag_list = await self.flags.listAllFlags(user,user.id)
        if flag_list is not None:
            flag_str_list = []
            for flag in flag_list:
                flag_key = flag[0]
                flag_value = flag[1]
                flag_expires = flag[2]
                flag_str_list.append(f"**{flag_key}**\n`{flag_value}`\n**Истекает** <t:{flag_expires}:R>\n")
            # flag_list = [flag[0] for flag in flag_list]
            await inter.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay(f"Флаги пользователя {user.mention}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay("\n".join(flag_str_list))
            ), allowed_mentions=disnake.AllowedMentions(users=False))
        else:
            await inter.send("У данного пользователя нет флагов.")
    
    @commands.slash_command(name='get_user_flag', description="Показать значение определенного флага пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def getUserFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                            flag: str,
                            user: disnake.Member):
        flag_info = await self.flags.getFlag(user,user.id,flag)
        if flag_info:
            value, expires_at = flag_info
            value_str = f"Значение: `{value}`\nИстекает: <t:{expires_at}:R>" if expires_at else f"Значение: `{flag_info[0]}`"
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
        flag_list = await self.flags.listAllFlags(channel,channel.id)
        if flag_list is not None:
            flag_str_list = []
            for flag in flag_list:
                flag_key = flag[0]
                flag_value = flag[1]
                flag_expires = flag[2]
                flag_str_list.append(f"**{flag_key}**\n`{flag_value}`\n**Истекает** <t:{flag_expires}:R>\n")
            # flag_list = [flag[0] for flag in flag_list]
            await inter.send(components=disnake.ui.Container(
                disnake.ui.TextDisplay(f"Флаги пользователя {channel.mention}"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay("\n".join(flag_str_list))
            ), allowed_mentions=disnake.AllowedMentions(users=False))
        else:
            await inter.send("У данного пользователя нет флагов.")

    @commands.slash_command(name='flag_user', description="Добавить флаг на пользователя")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def addUserFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                            member: disnake.Member,
                            flag: str,
                            value = None,
                            expires_at = None):
        if member:
            await self.flags.setFlag(member,member.id,flag,value,expires_at)
        else:
            await inter.send("Такого пользователя нет!", ephemeral=True)

    @commands.slash_command(name='flag_channel', description="Добавить флаг на канал")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def addChannelFlagCommand(self, inter: disnake.ApplicationCommandInteraction,
                            channel: disnake.abc.GuildChannel,
                            flag: str,
                            value = None,
                            expires_at = None):
        if channel:
            await self.flags.setFlag(channel,channel.id,flag,value,expires_at)
        else:
            await inter.send("Такого канала нет!", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(FlagCommands(bot))