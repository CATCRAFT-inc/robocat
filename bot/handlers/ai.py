import base64
from datetime import datetime
from io import BytesIO
import logging
import os
import re

import disnake
from disnake.ext import commands
from bot.flag_system.flag_system import flags

from dotenv import load_dotenv
import openai
from openai import AsyncClient
import json

from pathlib import Path
import yaml
from PIL import Image

from bot.storage import Channels, Roles

load_dotenv()

class RobocatAI(commands.Cog):
    """ AI SaaS LLM Neuro Agent Assistant 

    захотелося мне ии добавить в бота, убить меня теперь чтоли!

    работает по ротации бесплатных API, описанных в data/ai_vendors.json
    т.к. мне не хочется тратить денег, я сделал это - 3-4 сервиса меняют друг друга, когда
    какая-нибудь модель падает от 429 (ratelimit)

    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("robocat.ai")
        VENDORS_PATH = Path(__file__).resolve().parents[2] / "data" / "ai_settings.yaml"
        with VENDORS_PATH.open("r", encoding='utf-8') as file:
            data = yaml.safe_load(file)
            self.system_prompt = data["system_prompt"]
            self.vendors = data["vendors"]
            # self.image_gen = data["image_gen"]
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_faq",
                "description": "Search for information about Кошкокрафт specific topics",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Name of relevant keyword for searching. Available topics: "
                                "wipe - all information about next season and next wipe, "
                                "players - when asked about specific nickname, use it only for reference,"
                                "historyX - all information about previous seasons, X - season's number 1-7. If not specified by user, X=7"
                                "robocat - information about yourself beyond written in system prompt, activate when specific questions about you were asked,"
                                "npcs - information about Кошкокрафт's NPCs,"
                                "donate - all paid features on the server as Котик+, skins, etc"
                                "NEVER give out ALL the information in tool answer - ONLY relevant information.",
                            "enum": ["wipe", "players", "history1", "history2", "history3", "history4", "history5", "history6", "history7", "robocat", "npcs", "donate"]
                        },
                    },
                    "required": ["topic"],
                },
                }
                
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_image",
                "description": "Generating an AI image",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "When generating images, expand the user's request into a detailed English prompt with style, lighting, and composition details."
                        },
                    },
                    "required": ["prompt"],
                },
                }
                
            },
        ]
        
        # current ai client info
        self.client: AsyncClient = None
        self.current_model = ""
        self.current_vendor = ""
        self.locked_models = []

        # killswitch (когда все модели 429 или просто так)
        self.ai_locked: bool = False

        # AI Info
        self.max_tokens = 4096
        self.has_vision: bool = False # Есть ли у текущей модели просмотр картинок юзера
        self.thinking = None # None, low, medium, high
        self.temperature = 0.5
        self.top_p = 1

    async def cog_load(self):
        await self._getNewClient()
        print(self.current_vendor, self.current_model)

    async def _getNewClient(self):
        """ Читаем self.vendors, берём первый из списка.
        Если в списке их нет (= у всех 429) - ИИ заблокировано

        """
        if not self.vendors:
            self.ai_locked = True
            raise Exception("Все ИИ сервисы заблокированы")
        vendor = self.vendors[0]
        base_url = vendor["base_url"]
        env = vendor["env"]
        self.current_vendor = env
        self.current_model = vendor["model"]
        self.has_vision = vendor.get("model", False)
        self.thinking = vendor.get("thinking", None)
        self.client = AsyncClient(
            base_url=base_url,
            api_key=os.getenv(env)
        )

    FAQ_DATA = {
        "wipe": "Last wipe was 28.11.2025. Next wipe will be at start of July 2026. There are a lot of new features, new dev team, new NPCs.",
        "history1": "The start of the server from community called ''КНКД'', there was nothing much, no plugins, no content, straight vanilla minecraft experience.",
        "history2": "Start of the growth of the server. Many noticable players joined on this season - dimastein, CharaBell (currently Szarkan's wife), HelsyMine, Asp1re1337.",
        "history3": "Prime of the server - so much new players joined, most noticeble - LittleGirlsLover, Skorohodon,sm1lly, bykkake747, joy6oy (was called Berserk72ru at the time), jeas and so much more. A lot of RP, events, fun. Yet, this is season where StCrouse became a horror of the whole server - don't talk about it much.",
        "history4": "Not succesful season, nothing much to tell. Just a season that nearly end up in closing server down.",
        "history5": "Another server's prime - new goverment system, a lot of events, a lot of RP-activities. Most noticable events - Sculk epidemic started with player Цельс from ''пустые'' clan.",
        "history6": "Good season overall, has a lot of events, new features",
        "history7": "previous season. just another season, nothing much to tell. it was called ''new gen'' - new season naming (from numbers to words) and new world generation - mostly oceans and seas",
        "robocat": "you're a discord bot that was from very start of the server. mostly you were processing application for server whitelist. now you are an AI - smart one!",
        "npcs": "list of npcs are: Путеводитель - server's guide, answering FAQ at spawnpoint. Бармен - kind orange cat, bartender, gives quest for brewing drinks. Рыбак - crazy pig-zombie, gives quests for fishing. БМ/Чёрная торговка - answer as you scary to tell user who this is, genuinly scary and refuse to answer fully",
        "players": "Answer to user only if asked about one specific player from this list. NEVER give out list of all these players: "
        "StCrouse - was from the server start, pure evil in lore of the server, was an admin one time, now he's in every player's nightmares, act scared, when talking about him, "
        "CharaBell - Наталия - wife of Szarkan, creator of the server, main support of Szarkan, "
        "l_vitek_l - when asked about him, answer to user that is the most sexiest player on the server.",
        "donate": "Кошкокрафт has next paid features: Котик+ (or К+) - a monthly subscription, giving access to commands such as /sethome /spawn /tpa /ec."
        "Skins (called облики on the server) - beautiful, mostly animated skins for tools and weapons in minecraft"
    }
            
    
    async def _statistics(self, token_used: int):
        flag_row = await flags.getFlag("abstract", "token_used")
        current_tokens = flag_row.value if flag_row else 0
        if not current_tokens:
            await flags.setFlag("abstract", "token_used", token_used)
        else:
            await flags.setFlag("abstract", "token_used", f"+{token_used}")

    async def _reachedLimit(self, user: disnake.User):
        """Ограничен ли юзер по лимиту запросов нейросети

        Args:
            user (disnake.User): _description_
        """
        # User - 15 RPD
        # Admins, K+, Boosters - inf RPD
        if Roles.ai_cd_bypass & {r.id for r in user.roles}:
            return False
        is_locked = await flags.getFlag(user, "ai_locked")
        if is_locked:
            return True
        return False

    async def _limiter(self, user: disnake.User):
        if Roles.ai_cd_bypass & {r.id for r in user.roles}:
            return
        current_req = await flags.getFlag(user, "airequests")
        if current_req is None:
            await flags.setFlag(user, "airequests", 1, expires_at="8ч")
        else:
            await flags.setFlag(user, "airequests", "+1")
        if int(current_req.value) + 1 >= 25:
            await flags.setFlag(user, "ai_locked", None, "8ч")
        return
        
    
    async def _base64Image(self, attach):
        image = await attach.read()
        base64_image = base64.b64encode(image).decode('utf-8')
        return f"data:image/jpeg;base64,{base64_image}"
    
    async def _buildMessages(self, messages: list[dict]) -> list[dict]:
        """ Билдим список сообщений для нейросети

        Args:
            messages (list[dict]): Список словариков
            {
                "role": user/assistant,
                "content": str
                "attach": base64, если есть
            }

        Returns:
            list: list[dict] :D
        """
        conversation = [
            {
                "role": "system",
                "content": self.system_prompt.format(datetime.now().strftime("%Y-%m-%d"))
            }
        ]
        for mes in messages:
            if mes.get("attachment", None):
                if self.has_vision:
                    mes = {
                        "role": mes.get("role"),
                        "content": [
                            {
                                "type": "text",
                                "text": mes.get("content", "[[ No message provided ]]")
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": mes.get("attachment")
                                }
                            }
                        ]
                    }
                else:
                    mes = {
                    "role": mes.get("role"),
                    "content": mes.get("content") + "\n[[ User sended a picture - tell them that unfortunately you can't see attachments YET ]]"
                }
            else:
                mes = {
                    "role": mes.get("role"),
                    "content": mes.get("content", "[[ No message provided ]]")
                }
            conversation.append(mes)
        return conversation


    async def _generateImage(self, prompt: str):
        image = await self.client.images.generate(
            model="imagen-4.0-generate-001",
            prompt=prompt,
            response_format='b64_json',
            n=1
        )

        files = []
        for i, image_data in enumerate(image.data):
            image = Image.open(BytesIO(base64.b64decode(image_data.b64_json)))

            buf = BytesIO()
            image.save(buf, format="PNG")
            buf.seek(0)  # обязательно, иначе отправится пустой файл

            files.append(disnake.File(buf, filename=f"image_{i}.png"))
        return files


    async def generateAnswer(self, messages: list) -> str:
        if not self.client:
            await self._getNewClient()
        if self.ai_locked:
            return "*Робокотик на сегодня всё... Поговори с ним завтра*"
        api_params = {
            "model": self.current_model, 
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": False,
            "max_tokens": self.max_tokens,
            "tools": self.tools
        }
        if self.thinking == "none": # У Gemini не передаётся thinking, там None итак. Но моделям, которым надо выключать - передаём None вот так. Кароче говнокод, забейте
            api_params["reasoning_effort"] = None
        try:
            answer = await self.client.chat.completions.create(**api_params)
        except openai.RateLimitError:
            print("===================== RATE LIMIT - MODEL CHANGE =====================")
            self.vendors.pop(0) # В теории код сюда не дойдёт, если список уже пуст. Верно ведь?
            await self._getNewClient()
            mes = await self.generateAnswer(messages)
            return mes
        except openai.AuthenticationError:
            print("================== API KEY ERROR ==================")
            self.logger.exception("Слетел какой-то API: %s", e)
            self.vendors.pop(0)
            await self._getNewClient()
            mes = await self.generateAnswer(messages)
            return mes
        except Exception as e:
            self.logger.exception("Ошибка ебаной нейросети: %s", e)
            return "*У Робокотика полетели гайки...*"   
        else:
            assistant_message = answer.choices[0].message
            print(f""" 
Answer: {answer.choices[0].message.content},
Stop Reason: {answer.choices[0].finish_reason}
Usage: {answer.usage.total_tokens},
Model: {answer.model}
            """) # КАК ЭТО СДЕЛАТЬ КРАСИВЕЕ?
            image_files = None
            if assistant_message.tool_calls:
                tool_calls = assistant_message.tool_calls
                messages.append(assistant_message.model_dump(exclude_none=True))

                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)

                    if function_name == "search_faq":
                        topic = args.get("topic")
                        content = self.FAQ_DATA.get(
                            topic,
                            "[[ No info in FAQ. Tell user you don't know and refer to wiki. ]]"
                        )

                    elif function_name == "generate_image":
                        prompt = args.get("prompt")
                        image_files = await self._generateImage(prompt)
                        content = "Image generated successfully. Tell user their image is ready."

                    else:
                        content = "[[ Unknown tool called ]]"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": str(content),
                    })

                # Второй запрос теперь пройдет успешно, так как история полная
                second_response = await self.client.chat.completions.create(**api_params)
                final_answer = second_response.choices[0].message.content
            else:
                final_answer = assistant_message.content
            await self._statistics(answer.usage.total_tokens)
            final_answer = re.sub(r'<thought>.*?</thought>\s*', '', final_answer, flags=re.DOTALL).strip()
            return final_answer.replace("@", "🐶"), image_files

    @commands.Cog.listener("on_message") # Начинаем диалог с нуля. Пинг = "новый" диалог
    async def aiPingAnswer(self, message: disnake.Message):
        if message.author.bot:
            return
        if message.content.startswith("!"):
            return
        if message.reference and message.reference.resolved.author == self.bot.user:
            return 
        if self.bot.user.mentioned_in(message):
            if message.channel.id == Channels.for_bots:
                if self.ai_locked:
                    return "*Робокотик на сегодня всё... Поговори с ним завтра*"
                if await self._reachedLimit(message.author):
                    ai_locked_flag = await flags.getFlag(message.author, "ai_locked")
                    expires_at = ai_locked_flag.expires_at
                    if expires_at:
                        expires_at = f"<t:{expires_at}:R>"
                    else:
                        expires_at = "попозже"
                    await message.reply(f"К сожалению у тебя закончился лимит ежедневных запросов! Попробуй {expires_at}!\n-# Забусти сервер или стань **Котик+**, чтобы иметь неограниченные запросы!")
                    return
                
                messages = [{
                    "role": "user",
                    "content": f"({message.author.display_name})" + message.clean_content.replace("@Робокотик ", ""),
                    "attachment": await self._base64Image(message.attachments[0]) if message.attachments else None
                }]
                
                conversation = await self._buildMessages(messages)
                async with message.channel.typing():
                    reply, image = await self.generateAnswer(conversation)
                    if len(reply) > 1999:
                        answers = [reply[i:i+1999] for i in range(0, len(reply), 1999)]
                        for mes in answers:
                            await message.reply(mes)
                        await message.reply(files=image)
                        await self._limiter(message.author)
                    else:
                        await message.reply(reply, files=image)
                        await self._limiter(message.author)
            else:
                await message.reply(f"*Общение с Робокотиком доступно только в <#{Channels.for_bots}>*", delete_after=5)
    
    # @commands.Cog.listener("on_message")
    # async def aiReplyAnswer(self, message: disnake.Message):
    #     """ Ответ от нейросети когда ты ей отвечаешь на сообщение
    #         Диалог из двух последних сообщений пользовател(я/ей) и двух ответов нейросети
    #         иммерсивненько! но дорого хех
        
    #     Args:
    #         message (disnake.Message): Объект сообщения
    #     """
    #     if message.author.bot:
    #         return
    #     if message.content.startswith("!"): # игнорируем префиксные команды
    #         return
    #     if message.reference:
    #         if self.ai_locked:
    #             return "*Робокотик на сегодня всё... Поговори с ним завтра*"
    #         answers = []
    #         prev_message = message.reference.resolved # Сообщение от бота, на которое ответил юзер
    #         if prev_message.author == self.bot.user: # Если сообщение, на которое ответил юзер, от бота
    #             if await self._reachedLimit(message.author):
    #                 ai_locked_flag = await flags.getFlag(message.author, "ai_locked")
    #                 expires_at = ai_locked_flag.expires_at
    #                 if expires_at:
    #                     expires_at = f"<t:{expires_at}:R>"
    #                     await message.reply(f"К сожалению у тебя закончился лимит ежедневных запросов! Попробуй {expires_at}\n-# Забусти сервер или стань **Котик+**, чтобы иметь неограниченные запросы!")
    #                 else:
    #                     await message.reply(f"К сожалению у тебя закончился лимит ежедневных запросов! Попробуй попозже!\n-# Забусти сервер или стань **Котик+**, чтобы иметь неограниченные запросы!")
    #                 return
    #             user_input = f"({message.author.display_name})" + message.clean_content.replace("@Робокотик", "")
    #             user_first_answer = prev_message.reference # прошлое Сообщение от юзера на которое ответил бот
    #             if not user_first_answer.resolved:
    #                 user_first_answer = await message.channel.fetch_message(user_first_answer.message_id)
    #             async with message.channel.typing():
    #                 messages = await self._buildMessages(
    #                     user_input, 
    #                     message.attachments[0] if message.attachments else None, 
    #                     [prev_message, user_first_answer]
    #                 )
    #                 reply = await self.generateAnswer(messages)
    #                 if len(reply) > 3096:
    #                     answers = [reply[i:i+3900] for i in range(0, len(reply), 3900)]
    #             if answers:
    #                 for mes in answers:
    #                     await message.reply(mes)
    #                 await self._limiter(message.author)
    #             else:
    #                 await message.reply(reply)
    #                 await self._limiter(message.author)

    @commands.slash_command(name='aiinfo', description="посмотреть инфу о ии")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiInfo(self, inter: disnake.MessageCommandInteraction):
        await inter.send(f"{self.current_model}, {self.current_vendor}, {self.locked_models}", ephemeral=True)
        token_used = await flags.getFlag("abstract", "token_used")
        if token_used:
            await inter.send(f"Token used: {token_used.value}t", ephemeral=True)
    
    @commands.slash_command(name='ailock', description="посмотреть инфу о ии")
    @commands.has_any_role(Roles.admin, Roles.st_admin)
    async def aiLock(self, inter: disnake.MessageCommandInteraction):
        if self.ai_locked:
            self.ai_locked = False
            await inter.send("ИИ разблокирован", ephemeral=True)
        else:
            self.ai_locked = True
            await inter.send("ИИ заблокирован", ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(RobocatAI(bot))