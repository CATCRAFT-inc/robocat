import disnake
from disnake.ext import commands

from bot.storage import Buttons


class GetAJob(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    JOB_EMBED = disnake.ui.Container(
        disnake.ui.TextDisplay("# Работа!"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay("В Кошкоземье всегда есть работа для тебя, начиная от гидов, которые помогают освоиться новоприбывшим, заканчивая **Всекотятами**, следящими за порядком на наших землях."),
        disnake.ui.TextDisplay("Узнай список открытых вакансий нажав кнопку ниже!"),
        disnake.ui.ActionRow(
            Buttons.GET_A_JOB.component
        )
    )

    @commands.Cog.listener("on_button_click")
    async def getAJobButton(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id == Buttons.GET_A_JOB.id:
            await inter.send(
                "Пока вакансий нет — сезон ещё не начался! Загляни позже =)",
                ephemeral=True,
            )


def setup(bot: commands.Bot):
    bot.add_cog(GetAJob(bot))