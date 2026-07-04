import disnake
from disnake.ext import commands


class RoleSelect(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_dropdown")
    async def roleSelectDropown(self, 
            inter: disnake.MessageInteraction):
        if inter.component.custom_id == "ROLESELECT":
            selected_role = inter.resolved_values
            role = inter.guild.get_role(int(selected_role[0]))
            if role:
                try:
                    if role in inter.author.roles:
                        await inter.author.remove_roles(role, reason="Убрал роль в канале выбора ролей")
                        await inter.send(f"Роль {role.mention} убрана!", ephemeral=True)
                    else:
                        await inter.author.add_roles(role, reason="Выбрал роль в канале выбора ролей")
                        await inter.send(f"Роль {role.mention} успешно выдана!", ephemeral=True)
                except disnake.Forbidden:
                    await inter.send("У меня не хватает прав на эту роль — сообщи админам!", ephemeral=True)
            else:
                await inter.send("Ой-ёй! Бот не нашёл нужную роль... Сообщи пожалуйста в **баг-репорт**!", ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(RoleSelect(bot))