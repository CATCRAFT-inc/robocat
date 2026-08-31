"""Вакансии: `/vacancy` → превью → публикация → приватный тред отклика."""

import asyncio
import itertools
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass

import disnake
from disnake.ext import commands

from bot import metrics as telemetry
from bot.discord_config import Channels, Roles, has_config_roles
from bot.flag_system.flag_system import flags
from bot.storage import Buttons, ColorStorage
from bot.utils import AnsweringModal

logger = logging.getLogger("robocat.get_a_job")

_CACHE_FLAG = "vacancies"
_APPLY_ID = "VACANCY_APPLY"
_TEXT_TYPES = (disnake.ui.TextDisplay, disnake.TextDisplay)


def _admin_roles() -> set[int]:
    return {Roles.admin, Roles.st_admin}


@dataclass
class _Vacancy:
    id: str
    title: str
    description: str
    requirements: str
    tasks: str
    payment: str
    updated_at: int


@dataclass
class _Draft:
    author_id: int
    title: str = ""
    description: str = ""
    requirements: str = ""
    tasks: str = ""
    payment: str = ""
    template_id: str | None = None


def _vacancy_container(vacancy: _Vacancy | _Draft, *, apply_enabled: bool) -> disnake.ui.Container:
    return disnake.ui.Container(
        disnake.ui.TextDisplay(f"# {vacancy.title}"),
        disnake.ui.TextDisplay(f"## Описание\n{vacancy.description}"),
        disnake.ui.TextDisplay(f"## Требования\n{vacancy.requirements}"),
        disnake.ui.TextDisplay(f"## Задачи\n{vacancy.tasks}"),
        disnake.ui.TextDisplay(f"## Оплата\n{vacancy.payment}"),
        disnake.ui.ActionRow(
            disnake.ui.Button(
                label="Откликнуться",
                custom_id=_APPLY_ID,
                style=disnake.ButtonStyle.success,
                disabled=not apply_enabled,
            )
        ),
        accent_colour=disnake.Color.from_hex(ColorStorage.main),
    )


def _encode_vacancies(vacancies: list[_Vacancy]) -> str:
    return json.dumps([asdict(vacancy) for vacancy in vacancies], ensure_ascii=False)


def _decode_vacancies(raw: str | None) -> list[_Vacancy]:
    if raw is None:
        return []
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise TypeError
        vacancies = [_Vacancy(**item) for item in payload if isinstance(item, dict)]
        if len(vacancies) != len(payload):
            raise TypeError
        for vacancy in vacancies:
            texts = (
                vacancy.id,
                vacancy.title,
                vacancy.description,
                vacancy.requirements,
                vacancy.tasks,
                vacancy.payment,
            )
            if not all(isinstance(value, str) and value for value in texts):
                raise TypeError
            if isinstance(vacancy.updated_at, bool) or not isinstance(vacancy.updated_at, int):
                raise TypeError
        return vacancies
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError("Повреждён кэш вакансий") from exc


def _message_vacancy_title(message) -> str:
    components = getattr(message, "components", ())
    children = getattr(components[0], "children", ()) if components else ()
    if children and isinstance(children[0], _TEXT_TYPES):
        title = (children[0].content or "").removeprefix("# ").strip()
        if title:
            return title
    return "Вакансия"


def _autocomplete_label(vacancy: _Vacancy) -> str:
    suffix = (
        f" — {time.strftime('%d.%m.%Y', time.localtime(vacancy.updated_at))} "
        f"· {vacancy.id[:6]}"
    )
    return f"{vacancy.title[:100 - len(suffix)]}{suffix}"


class VacancyModal(AnsweringModal):
    def __init__(self, cog: "GetAJob", draft_id: int):
        self.cog = cog
        self.draft_id = draft_id
        draft = cog.drafts[draft_id]
        components = [
            disnake.ui.Label(
                text="Название профессии",
                component=disnake.ui.TextInput(
                    custom_id="vacancy_title", value=draft.title or None, max_length=100
                ),
            ),
            disnake.ui.Label(
                text="Описание профессии",
                component=disnake.ui.TextInput(
                    custom_id="vacancy_description",
                    style=disnake.TextInputStyle.paragraph,
                    value=draft.description or None,
                    max_length=1000,
                ),
            ),
            disnake.ui.Label(
                text="Требования",
                component=disnake.ui.TextInput(
                    custom_id="vacancy_requirements",
                    style=disnake.TextInputStyle.paragraph,
                    value=draft.requirements or None,
                    max_length=1000,
                ),
            ),
            disnake.ui.Label(
                text="Задачи",
                component=disnake.ui.TextInput(
                    custom_id="vacancy_tasks",
                    style=disnake.TextInputStyle.paragraph,
                    value=draft.tasks or None,
                    max_length=1000,
                ),
            ),
            disnake.ui.Label(
                text="Оплата",
                component=disnake.ui.TextInput(
                    custom_id="vacancy_payment",
                    style=disnake.TextInputStyle.paragraph,
                    value=draft.payment or None,
                    max_length=500,
                ),
            ),
        ]
        super().__init__(
            title="Вакансия", custom_id=f"vacancy_modal:{draft_id}", components=components
        )

    async def callback(self, inter: disnake.ModalInteraction):
        draft = self.cog.drafts.get(self.draft_id)
        if draft is None:
            await inter.response.send_message(
                "Черновик потерян — начни заново: /vacancy", ephemeral=True
            )
            return

        draft.title = inter.text_values["vacancy_title"].strip()
        draft.description = inter.text_values["vacancy_description"].strip()
        draft.requirements = inter.text_values["vacancy_requirements"].strip()
        draft.tasks = inter.text_values["vacancy_tasks"].strip()
        draft.payment = inter.text_values["vacancy_payment"].strip()
        if not all(
            (draft.title, draft.description, draft.requirements, draft.tasks, draft.payment)
        ):
            components = self.cog._edit_only(
                self.draft_id, "Все пять полей должны содержать текст."
            )
        else:
            components = self.cog._preview(draft, self.draft_id)

        if inter.message is not None:
            await inter.response.edit_message(components=components)
        else:
            await inter.response.send_message(components=components, ephemeral=True)


class JobApplicationModal(AnsweringModal):
    def __init__(self, cog: "GetAJob", vacancy_message):
        self.cog = cog
        self.vacancy_message = vacancy_message
        super().__init__(
            title="Отклик на вакансию",
            custom_id=f"vacancy_apply:{vacancy_message.id}",
            components=[
                disnake.ui.Label(
                    text="Расскажи о себе",
                    description="Опыт, мотивация и всё, что поможет принять решение",
                    component=disnake.ui.TextInput(
                        custom_id="about",
                        style=disnake.TextInputStyle.paragraph,
                        min_length=20,
                        max_length=1500,
                        placeholder="Расскажи, почему эта вакансия тебе подходит",
                    ),
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        about = inter.text_values["about"].strip()
        if len(about) < 20:
            await inter.response.send_message(
                "Расскажи о себе чуть подробнее — нужно хотя бы 20 непробельных символов.",
                ephemeral=True,
            )
            return
        await self.cog._apply(
            inter,
            vacancy_message=self.vacancy_message,
            about=about,
        )


class JobDecisionModal(AnsweringModal):
    def __init__(self, cog: "GetAJob", applicant_id: int, *, accepted: bool):
        self.cog = cog
        self.applicant_id = applicant_id
        self.accepted = accepted
        action = "Принять" if accepted else "Отклонить"
        super().__init__(
            title=f"{action} отклик",
            custom_id=f"vacancy_decision:{'accept' if accepted else 'decline'}:{applicant_id}",
            components=[
                disnake.ui.Label(
                    text="Комментарий кандидату",
                    component=disnake.ui.TextInput(
                        custom_id="note",
                        style=disnake.TextInputStyle.paragraph,
                        required=False,
                        max_length=1000,
                    ),
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)
        await self.cog._decide_application(
            inter,
            applicant_id=self.applicant_id,
            accepted=self.accepted,
            note=(inter.text_values.get("note") or "").strip() or None,
        )


class GetAJob(commands.Cog):
    JOB_EMBED = disnake.ui.Container(
        disnake.ui.TextDisplay("# Работа!"),
        disnake.ui.Separator(),
        disnake.ui.TextDisplay(
            "В Кошкоземье всегда есть работа для тебя, начиная от гидов, которые "
            "помогают освоиться новоприбывшим, заканчивая **Всекотятами**, "
            "следящими за порядком на наших землях."
        ),
        disnake.ui.TextDisplay("Узнай список открытых вакансий, нажав кнопку ниже!"),
        disnake.ui.ActionRow(Buttons.GET_A_JOB.component),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drafts: dict[int, _Draft] = {}
        self._seq = itertools.count(int(time.time()))
        self._cache_lock = asyncio.Lock()
        self._applying: set[tuple[int, int]] = set()
        self._deciding: set[int] = set()

    def _preview(self, draft: _Draft, draft_id: int) -> list:
        return [
            disnake.ui.TextDisplay(
                f"-# Превью: так вакансия будет опубликована в <#{Channels.jobs}>"
            ),
            _vacancy_container(draft, apply_enabled=False),
            disnake.ui.ActionRow(
                disnake.ui.Button(
                    label="Опубликовать",
                    custom_id=f"VACANCY_PUB:{draft_id}",
                    style=disnake.ButtonStyle.success,
                ),
                disnake.ui.Button(
                    label="Править",
                    custom_id=f"VACANCY_EDIT:{draft_id}",
                    style=disnake.ButtonStyle.secondary,
                ),
                disnake.ui.Button(
                    label="Отмена",
                    custom_id=f"VACANCY_CANCEL:{draft_id}",
                    style=disnake.ButtonStyle.danger,
                ),
            ),
        ]

    @staticmethod
    def _edit_only(draft_id: int, reason: str) -> list:
        return [
            disnake.ui.TextDisplay(f"{reason} Текст не потерян — жми «Править»."),
            disnake.ui.ActionRow(
                disnake.ui.Button(
                    label="Править",
                    custom_id=f"VACANCY_EDIT:{draft_id}",
                    style=disnake.ButtonStyle.secondary,
                )
            ),
        ]

    async def _load_vacancies(self) -> list[_Vacancy]:
        row = await flags.getFlag("abstract", _CACHE_FLAG)
        return _decode_vacancies(row.value if row else None)

    async def _cache_vacancy(self, draft: _Draft) -> _Vacancy:
        vacancy = _Vacancy(
            id=draft.template_id or uuid.uuid4().hex,
            title=draft.title,
            description=draft.description,
            requirements=draft.requirements,
            tasks=draft.tasks,
            payment=draft.payment,
            updated_at=int(time.time()),
        )
        async with self._cache_lock:
            vacancies = await self._load_vacancies()
            for index, stored in enumerate(vacancies):
                if stored.id == vacancy.id:
                    vacancies[index] = vacancy
                    break
            else:
                vacancies.append(vacancy)
            saved = await flags.setFlag("abstract", _CACHE_FLAG, _encode_vacancies(vacancies))
            if not saved:
                raise RuntimeError("Не удалось сохранить кэш вакансий")
        draft.template_id = vacancy.id
        return vacancy

    @commands.slash_command(
        name="vacancy", description="Создать новую или повторно опубликовать вакансию"
    )
    @has_config_roles("admin", "st_admin")
    async def vacancy(
        self,
        inter: disnake.AppCmdInter,
        template_id: str = commands.Param(
            default=None,
            name="вакансия",
            description="Сохранённая вакансия для повторной публикации",
        ),
    ):
        draft_id = next(self._seq)
        if template_id is None:
            self.drafts[draft_id] = _Draft(author_id=inter.author.id)
            await inter.response.send_modal(VacancyModal(self, draft_id))
            return

        try:
            vacancies = await self._load_vacancies()
        except Exception:
            logger.exception("Не удалось прочитать кэш вакансий")
            await inter.response.send_message(
                "Не удалось прочитать сохранённые вакансии. Попробуй позже.", ephemeral=True
            )
            return
        vacancy = next((item for item in vacancies if item.id == template_id), None)
        if vacancy is None:
            await inter.response.send_message(
                "Эта сохранённая вакансия не найдена.", ephemeral=True
            )
            return
        draft = _Draft(
            author_id=inter.author.id,
            title=vacancy.title,
            description=vacancy.description,
            requirements=vacancy.requirements,
            tasks=vacancy.tasks,
            payment=vacancy.payment,
            template_id=vacancy.id,
        )
        self.drafts[draft_id] = draft
        await inter.response.send_message(
            components=self._preview(draft, draft_id), ephemeral=True
        )

    @vacancy.autocomplete("вакансия")
    async def vacancyAutocomplete(self, inter: disnake.AppCmdInter, user_input: str):
        try:
            vacancies = await self._load_vacancies()
        except Exception:
            logger.exception("Autocomplete не прочитал кэш вакансий")
            return []
        query = user_input.casefold().strip()
        matches = [
            vacancy
            for vacancy in sorted(vacancies, key=lambda item: item.updated_at, reverse=True)
            if query in vacancy.title.casefold() or query in vacancy.id.casefold()
        ]
        return {
            _autocomplete_label(vacancy): vacancy.id
            for vacancy in matches[:25]
        }

    @commands.Cog.listener("on_button_click")
    async def getAJobButton(self, inter: disnake.MessageInteraction):
        if inter.component.custom_id == Buttons.GET_A_JOB.id:
            await inter.send(
                f"Все актуальные вакансии: <#{Channels.jobs}>", ephemeral=True
            )

    @commands.Cog.listener("on_button_click")
    async def vacancyButtons(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id or ""
        if custom_id == _APPLY_ID:
            await inter.response.send_modal(JobApplicationModal(self, inter.message))
            return
        if not custom_id.startswith("VACANCY_"):
            return
        if not any(role.id in _admin_roles() for role in getattr(inter.author, "roles", ())):
            await inter.response.send_message("Только для админов.", ephemeral=True)
            return

        action, _, raw_id = custom_id.partition(":")
        if action in {"VACANCY_ACCEPT", "VACANCY_DECLINE"}:
            if (
                not isinstance(inter.channel, disnake.Thread)
                or inter.channel.parent_id != Channels.jobs
            ):
                await inter.response.send_message(
                    "Эти кнопки работают только в тредах откликов.", ephemeral=True
                )
                return
            applicant_id = int(raw_id) if raw_id.isascii() and raw_id.isdigit() else 0
            if not applicant_id:
                await inter.response.send_message(
                    "Не удалось определить кандидата.", ephemeral=True
                )
                return
            await inter.response.send_modal(
                JobDecisionModal(
                    self,
                    applicant_id,
                    accepted=action == "VACANCY_ACCEPT",
                )
            )
            return
        draft_id = int(raw_id) if raw_id.isascii() and raw_id.isdigit() else -1
        draft = self.drafts.get(draft_id)
        if draft is None:
            await inter.response.edit_message(
                components=[
                    disnake.ui.TextDisplay(
                        "Черновик потерян (бот перезапускался?) — начни заново: /vacancy"
                    )
                ]
            )
            return
        if draft.author_id != inter.author.id:
            await inter.response.send_message(
                "Этот черновик не твой — начни свой: /vacancy", ephemeral=True
            )
            return

        if action == "VACANCY_EDIT":
            await inter.response.send_modal(VacancyModal(self, draft_id))
        elif action == "VACANCY_CANCEL":
            del self.drafts[draft_id]
            await inter.response.edit_message(
                components=[disnake.ui.TextDisplay("❌ Черновик удалён.")]
            )
        elif action == "VACANCY_PUB":
            claimed = self.drafts.pop(draft_id, None)
            if claimed is None:
                await inter.response.edit_message(
                    components=[disnake.ui.TextDisplay("Уже публикуется или опубликовано.")]
                )
                return
            await self._publish(inter, draft_id, claimed)

    async def _publish(
        self, inter: disnake.MessageInteraction, draft_id: int, draft: _Draft
    ) -> None:
        channel = self.bot.get_channel(Channels.jobs)
        if channel is None:
            self.drafts[draft_id] = draft
            await inter.response.edit_message(
                components=[
                    disnake.ui.TextDisplay("😞 Канал вакансий не найден."),
                    *self._preview(draft, draft_id),
                ]
            )
            return

        try:
            await inter.response.edit_message(
                components=[disnake.ui.TextDisplay("⏳ Публикую...")]
            )
        except disnake.HTTPException:
            self.drafts[draft_id] = draft
            raise

        try:
            message = await channel.send(
                components=[_vacancy_container(draft, apply_enabled=True)],
                allowed_mentions=disnake.AllowedMentions.none(),
            )
        except disnake.HTTPException:
            logger.exception("Не удалось опубликовать вакансию в канал %s", Channels.jobs)
            self.drafts[draft_id] = draft
            await inter.edit_original_response(
                components=[
                    disnake.ui.TextDisplay("😞 Не получилось опубликовать — попробуй ещё раз."),
                    *self._preview(draft, draft_id),
                ]
            )
            return

        try:
            await self._cache_vacancy(draft)
        except Exception:
            logger.exception("Вакансия %s опубликована, но не попала в кэш", message.id)
            await inter.edit_original_response(
                components=[
                    disnake.ui.TextDisplay(
                        f"⚠️ Опубликовано: {message.jump_url}, но сохранить шаблон не удалось."
                    )
                ]
            )
            return
        await inter.edit_original_response(
            components=[disnake.ui.TextDisplay(f"✅ Опубликовано: {message.jump_url}")]
        )
        telemetry.record_later("job", "vacancy_published")

    async def _apply(
        self,
        inter: disnake.ModalInteraction,
        *,
        vacancy_message,
        about: str,
    ) -> None:
        key = (vacancy_message.id, inter.author.id)
        if key in self._applying:
            await inter.response.send_message(
                    "Отклик уже создаётся — подожди пару секунд.", ephemeral=True
            )
            return
        self._applying.add(key)
        try:
            await inter.response.defer(ephemeral=True)
        except disnake.HTTPException:
            self._applying.discard(key)
            raise
        thread = None
        try:
            try:
                channel = self.bot.get_channel(Channels.jobs)
                if channel is None:
                    await inter.edit_original_response(
                        "Не удалось найти канал вакансий. Сообщи администрации."
                    )
                    return
                title = _message_vacancy_title(vacancy_message)
                thread = await channel.create_thread(
                    name=f"{title} — {inter.author.display_name}"[:100],
                    type=disnake.ChannelType.private_thread,
                    auto_archive_duration=10080,
                    reason=f"Отклик на вакансию от {inter.author.id}",
                )
                container = disnake.ui.Container(
                    disnake.ui.TextDisplay(f"# Отклик на вакансию «{title}»"),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay(f"Кандидат: {inter.author.mention}"),
                    disnake.ui.TextDisplay(f"Вакансия: {vacancy_message.jump_url}"),
                    disnake.ui.TextDisplay(
                        f"## О себе\n{disnake.utils.escape_markdown(about)}"
                    ),
                    disnake.ui.TextDisplay(
                        f"-# ||{inter.author.mention} <@&{Roles.admin}> <@&{Roles.st_admin}>||"
                    ),
                    disnake.ui.ActionRow(
                        disnake.ui.Button(
                            style=disnake.ButtonStyle.green,
                            label="✅ Принять",
                            custom_id=f"VACANCY_ACCEPT:{inter.author.id}",
                        ),
                        disnake.ui.Button(
                            style=disnake.ButtonStyle.danger,
                            label="⛔ Отклонить",
                            custom_id=f"VACANCY_DECLINE:{inter.author.id}",
                        ),
                    ),
                    accent_colour=disnake.Color.from_hex(ColorStorage.main),
                )
                await thread.send(
                    components=[container],
                    allowed_mentions=disnake.AllowedMentions(
                        everyone=False,
                        users=[disnake.Object(id=inter.author.id)],
                        roles=[
                            disnake.Object(id=Roles.admin),
                            disnake.Object(id=Roles.st_admin),
                        ],
                    ),
                )
                telemetry.record_later("job", "application_created")
            except disnake.HTTPException:
                logger.exception("Не удалось создать отклик на вакансию %s", vacancy_message.id)
                if thread is not None:
                    try:
                        await thread.delete(reason="Карточка отклика не отправилась")
                    except disnake.HTTPException:
                        logger.exception("Не удалось удалить пустой тред отклика %s", thread.id)
                try:
                    await inter.edit_original_response(
                        "Не удалось создать отклик. Попробуй позже или сообщи администрации."
                    )
                except disnake.HTTPException:
                    logger.warning("Не удалось сообщить об ошибке отклика %s", vacancy_message.id)
                return

            try:
                await inter.edit_original_response(f"Отклик создан: <#{thread.id}>")
            except disnake.HTTPException:
                logger.warning(
                    "Отклик %s создан в треде %s, но ephemeral-ответ не доставлен",
                    vacancy_message.id,
                    thread.id,
                )
        finally:
            self._applying.discard(key)

    async def _decide_application(
        self,
        inter: disnake.ModalInteraction,
        *,
        applicant_id: int,
        accepted: bool,
        note: str | None,
    ) -> None:
        thread = inter.channel
        if thread.id in self._deciding:
            await inter.edit_original_response("Этот отклик уже обрабатывает другой админ.")
            return
        self._deciding.add(thread.id)
        try:
            candidate = inter.guild.get_member(applicant_id)
            if candidate is None:
                try:
                    candidate = await inter.guild.fetch_member(applicant_id)
                except disnake.NotFound:
                    candidate = None
                except disnake.HTTPException:
                    logger.exception("Не удалось найти кандидата %s", applicant_id)
                    candidate = None

            result = "принят" if accepted else "отклонён"
            emoji = "✅" if accepted else "⛔"
            note_text = (
                f"\n\n**Комментарий:**\n{disnake.utils.escape_markdown(note)}"
                if note
                else ""
            )
            dm_delivered = False
            if candidate is not None:
                try:
                    await candidate.send(
                        components=disnake.ui.Container(
                            disnake.ui.TextDisplay(
                                f"## {emoji} Твой отклик на вакансию {result}"
                            ),
                            disnake.ui.Separator(),
                            disnake.ui.TextDisplay(
                                f"Вакансия: **{disnake.utils.escape_markdown(thread.name)}**"
                                f"{note_text}"
                            ),
                            accent_colour=disnake.Color.from_hex(ColorStorage.main),
                        )
                    )
                    dm_delivered = True
                except disnake.HTTPException:
                    logger.info(
                        "Не удалось отправить решение по вакансии кандидату %s",
                        applicant_id,
                    )

            if not dm_delivered:
                await inter.edit_original_response(
                    "ЛС кандидату не доставлено — решение не применено, тред не удалён. "
                    "Свяжись с кандидатом и попробуй кнопку ещё раз."
                )
                return

            try:
                await thread.delete(
                    reason=f"Отклик {result} администратором {inter.author.id}"
                )
                telemetry.record_later("job", "accepted" if accepted else "declined")
            except disnake.HTTPException:
                logger.exception("Не удалось удалить тред отклика %s", thread.id)
                raise
            try:
                await inter.edit_original_response(
                    f"Отклик {result}. Кандидат уведомлён, тред удалён."
                )
            except disnake.HTTPException:
                # Решение уже доставлено, тред удалён; ephemeral-подтверждение
                # косметическое и не должно оставлять активные кнопки.
                logger.info(
                    "Решение по отклику %s применено, но подтверждение админу не доставлено",
                    thread.id,
                )
        finally:
            self._deciding.discard(thread.id)


def setup(bot: commands.Bot):
    bot.add_cog(GetAJob(bot))
    logger.info("Ког GetAJob загружен")
