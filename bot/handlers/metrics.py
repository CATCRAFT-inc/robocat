"""Сбор продуктовых событий и приватный дашборд для администрации."""

import logging

import aiosqlite
import disnake
from disnake.ext import commands

from bot import metrics as telemetry
from bot.discord_config import has_config_roles
from bot.utils import create_container


logger = logging.getLogger("robocat.metrics_cog")

_EVENT_LABELS = {
    ("ticket", "created"): "Создан тикет",
    ("ticket", "closed"): "Закрыт тикет",
    ("ticket", "declined"): "Отклонён тикет",
    ("job", "application_created"): "Создана заявка на вакансию",
    ("job", "vacancy_published"): "Опубликована вакансия",
    ("job", "accepted"): "Принята заявка на вакансию",
    ("job", "declined"): "Отклонена заявка на вакансию",
    ("security", "mention_raid"): "Антирейд по пингам",
    ("security", "honeypot"): "Срабатывание канала-ловушки",
}

_KIND_LABELS = {
    "prefix_command": "Префикс-команда",
    "prefix_error": "Ошибка префикс-команды",
    "slash_command": "Слэш-команда",
    "slash_error": "Ошибка слэш-команды",
    "button": "Кнопка",
    "dropdown": "Выбор в списке",
    "modal": "Отправлена форма",
}


def _command_name(command) -> str:
    raw = getattr(command, "qualified_name", None) or getattr(command, "name", None)
    return telemetry.normalize_event_name(raw)


def _component_name(inter) -> str:
    component = getattr(inter, "component", None)
    custom_id = getattr(component, "custom_id", None) or getattr(inter, "custom_id", None)
    return telemetry.normalize_event_name(custom_id)


def _render_snapshot(snapshot: telemetry.MetricsSnapshot, days: int | None) -> disnake.ui.Container:
    period = "за всё время" if days is None else ("сегодня" if days == 1 else f"за {days} дней")
    if snapshot.rows:
        lines = []
        for row in snapshot.rows[:20]:
            label = _EVENT_LABELS.get(
                (row.kind, row.name),
                f"{_KIND_LABELS.get(row.kind, row.kind)} `{row.name}`",
            )
            lines.append(f"**{label}:** {row.count}")
        description = "\n".join(lines)
    else:
        description = "Пока событий нет."

    return create_container(
        "## Метрики Robocat",
        description,
        footer=f"{snapshot.total} событий {period}; показаны топ-20 без текстов и Discord ID.",
    )


class MetricsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        telemetry.start_worker()

    def cog_unload(self):
        telemetry.stop_worker()

    @commands.Cog.listener("on_command_completion")
    async def prefixCommandCompleted(self, ctx):
        telemetry.record_later("prefix_command", _command_name(ctx.command))

    @commands.Cog.listener("on_command_error")
    async def prefixCommandFailed(self, ctx, _error):
        telemetry.record_later("prefix_error", _command_name(getattr(ctx, "command", None)))

    @commands.Cog.listener("on_slash_command_completion")
    async def slashCommandCompleted(self, inter):
        telemetry.record_later("slash_command", _command_name(inter.application_command))

    @commands.Cog.listener("on_slash_command_error")
    async def slashCommandFailed(self, inter, _error):
        telemetry.record_later(
            "slash_error", _command_name(getattr(inter, "application_command", None))
        )

    @commands.Cog.listener("on_button_click")
    async def buttonClicked(self, inter):
        telemetry.record_later("button", _component_name(inter))

    @commands.Cog.listener("on_dropdown")
    async def dropdownUsed(self, inter):
        telemetry.record_later("dropdown", _component_name(inter))

    @commands.Cog.listener("on_modal_submit")
    async def modalSubmitted(self, inter):
        telemetry.record_later("modal", _component_name(inter))

    @commands.slash_command(name="metrics", description="Показать продуктовые метрики Robocat")
    @has_config_roles("admin", "st_admin")
    async def metricsDashboard(
        self,
        inter: disnake.ApplicationCommandInteraction,
        days: int = commands.Param(
            default=7,
            choices={"Сегодня": 1, "7 дней": 7, "30 дней": 30, "Всё время": 0},
        ),
    ):
        await inter.response.defer(ephemeral=True)
        window = days or None
        try:
            snapshot = await telemetry.report_events(days=window)
        except aiosqlite.OperationalError:
            logger.exception("Таблица event_metrics ещё не создана")
            await inter.edit_original_response(
                content="Метрики ещё не инициализированы. Выполни `cd data && python db_init.py`."
            )
            return

        await inter.edit_original_response(
            content=None,
            components=[_render_snapshot(snapshot, window)],
        )


def setup(bot: commands.Bot):
    bot.add_cog(MetricsCog(bot))
    logger.info("Ког MetricsCog загружен")
