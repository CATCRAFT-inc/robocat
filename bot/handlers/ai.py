import base64
from datetime import datetime, timedelta
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
from .wiki_search import wiki

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
                    "name": "search_wiki",
                "description": "Search for information about Кошкокрафт from server's wiki via embedding",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Give a query for embedding model - either a raw user request or part of user request for embedding."
                        },
                    },
                    "required": ["query"],
                },
                }
                
            },
            # {
            #     "type": "function",
            #     "function": {
            #         "name": "generate_image",
            #     "description": "Generating an AI image",
            #     "parameters": {
            #         "type": "object",
            #         "properties": {
            #             "prompt": {
            #                 "type": "string",
            #                 "description": "When generating images, expand the user's request into a detailed English prompt with style, lighting, and composition details."
            #             },
            #         },
            #         "required": ["prompt"],
            #     },
            #     }
                
            # },
            {
                "type": "function",
                "function": {
                    "name": "user_info",
                "description": "Get current's user discord info (right now - only their roles)",
                "parameters": {}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "mute_user",
                "description": "Mute user if they misbehave, annoying or you just feel like it",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "duration": {
                            "type": "integer",
                            "description": "duration of mute in seconds. Max duration - 1209600s (14 days)"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason of mute"
                        },
                    },
                    "required": ["duration", "reason"],
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
        self.ai_locked_bypass_user_ids = [531208170098655233]

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
        self.has_vision = vendor.get("has_vision", False)
        self.thinking = vendor.get("thinking", None)
        self.client = AsyncClient(
            base_url=base_url,
            api_key=os.getenv(env)
        )
            
    
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
        # User - 35 RPD
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
        print(current_req)
        if current_req is None:
            await flags.setFlag(user, "airequests", 1, expires_at="8ч")
        else:
            await flags.setFlag(user, "airequests", "+1")
            if int(current_req.value) + 1 >= 35:
                await flags.setFlag(user, "ai_locked", None, "8ч")
    
    async def _base64Image(self, attach: disnake.Attachment):
        image = await attach.read()
        base64_image = base64.b64encode(image).decode('utf-8')
        return f"data:image/jpeg;base64,{base64_image}"
    
    async def _generateImage(self, prompt: str):
        image = await self.client.images.generate(
            model="imagen-4.0-ultra-generate-001",
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
    
    async def _buildConverstaion(self, messages: list[disnake.Message]) -> list[dict]:
        conversation = [{
            "role": "system",
            "content": self.system_prompt.format(datetime.now().date()) or "You're helpful assistant."
        }]
        print([i.content for i in messages])
        for mes in messages:
            role = ""
            content = ""
            match mes.author:
                case self.bot.user:
                    role = "assistant"
                    content = mes.clean_content
                    if mes.attachments:
                        attach_type = mes.attachments[0].content_type
                        content += f"[[ This message contained {attach_type} content, now it's not available ]]"
                    conversation.append({
                        "role": role,
                        "content": content
                    })
                case _:
                    role = "user"
                    content = f"({mes.author.display_name})" + mes.clean_content
                    if mes.attachments:
                        attachment = mes.attachments[0]
                        if attachment.content_type.split("/")[0] == "image": # image/png image/jpg image/gif(?)
                            processed_attachment = await self._base64Image(attachment)
                            conversation.append({
                                "role": role,
                                "content": [
                                        {
                                            "type": "text",
                                            "text": content
                                        },
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": processed_attachment
                                            }
                                        }
                                    ]
                            })
                        else:
                            content += "[[ User provided attachment that you can't process. Tell them about that politely.]]"
                    conversation.append({
                        "role": role,
                        "content": content
                    })
        return conversation
        
    async def generateAnswer(self, conversation: list, user_message: disnake.Message):
        if not self.client:
            await self._getNewClient()
        if self.ai_locked:
            return "*Робокотик на сегодня всё... Поговори с ним завтра*", None
        api_params = {
            "model": self.current_model, 
            "messages": conversation,
            "temperature": self.temperature or 0.5,
            "top_p": self.top_p or 1,
            "stream": False,
            "max_tokens": self.max_tokens or 2048,
            "tools": self.tools or None
        }
        try:
            response = await self.client.chat.completions.create(**api_params)
        except openai.RateLimitError:
            print("===================== RATE LIMIT =====================")
            self.vendors.pop(0) # В теории код сюда не дойдёт, если список уже пуст. Верно ведь?
            await self._getNewClient()
            mes, image_files, _ = await self.generateAnswer(conversation, user_message)
            return mes, image_files, None
        except openai.AuthenticationError:
            print("================== API KEY ERROR ==================")
            self.logger.exception("Слетел какой-то API: %s", e)
            self.vendors.pop(0)
            await self._getNewClient()
            mes, image_files, _ = await self.generateAnswer(conversation, user_message)
            return mes, image_files, None
        except openai.InternalServerError as e:
            self.logger.exception("Internal server error: %s", e)
            self.client = None
            mes, image_files, _ = await self.generateAnswer(conversation, user_message)
            return mes, image_files, None
        except openai.RateLimitError as e:
            self.logger.exception("Rate Limit: %s", e)
            return "*Пш-ш-ш-ш... Процессор робокотика перегрет от такого количества запросов! Попробуй поговорить с ним через минутку*", None, None
        except Exception as e:
            self.logger.exception("Ошибка нейросети: %s", e)
            return "*У Робокотика полетели гайки...*", None, None
        else:
            print(f""" 
            Answer: {response.choices[0].message.content[:100]},
            Stop Reason: {response.choices[0].finish_reason}
            Usage: {response.usage.total_tokens},
            Model: {response.model}
            """) 
            answer = response.choices[0].message
            attachment = None
            bot_thinking_message = None
            if answer.tool_calls:
                tool_calls = answer.tool_calls
                conversation.append(answer.model_dump(exclude_none=True))

                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    match function_name:
                        case "search_wiki":
                            bot_thinking_message = await user_message.reply(":book: Ищу по вики...")
                            try:
                                results = wiki.search(args.get("query"))
                            except:
                                content = "[[ Embedding raised an error - tell user that you can't find info right now and they should try later ]]"
                            else:
                                if results:
                                    content = wiki.build_context(results)
                        case "generate_image":
                            bot_thinking_message = await user_message.reply(":paintbrush: Создаю картинку...")
                            prompt = args.get("prompt")
                            attachment = await self._generateImage(prompt)
                            content = "[[ Image generated successfully. Tell user their image is ready. ]]"
                        case "user_info":
                            content = [i.name for i in user_message.author.roles]
                        case "mute_user":
                            duration = args.get("duration")
                            reason = args.get("reason")
                            try:
                                await user_message.author.timeout(duration=duration, reason=reason)
                            except Exception as e:
                                print(e)
                                content = "[[ Can't mute this user for some reason ]]"
                            else:
                                content = "[[ User is muted succesfully ]]"
                        case _:
                            content = "[[ Unknown tool called ]]"
                        

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": str(content),
                    })

                # Второй запрос теперь пройдет успешно, так как история полная
                second_response = await self.client.chat.completions.create(**api_params)
                print(second_response)
                print(json.dumps(conversation, indent=4, ensure_ascii=False))
                final_answer = second_response.choices[0].message.content
            else:
                final_answer = answer.content
            await self._statistics(response.usage.total_tokens)
            final_answer = re.sub(r'<thought>.*?</thought>\s*', '', final_answer, flags=re.DOTALL).strip()
            return final_answer.replace("@", "🐶"), attachment, bot_thinking_message


    @commands.Cog.listener("on_message")
    async def robocatAI(self, message: disnake.Message):
        if message.author.bot:
            return
        if message.content.startswith("!"):
            return
        if message.channel.id in [Channels.for_bots, Channels.secret]: # Отслеживаем сообщения только в двух чатах - для ботов и для теста
            if self.bot.user.mentioned_in(message) or (message.reference and message.reference.resolved.author == self.bot.user): # Если робокотика пинганули или ответили ему на сообщение
                if self.ai_locked and message.author.id not in self.ai_locked_bypass_user_ids:
                    return "*Робокотик остужает свой процессор... Поговори с ним попозже.*"
                if await self._reachedLimit(message.author):
                    ai_locked_flag = await flags.getFlag(message.author, "ai_locked")
                    expires_at = ai_locked_flag.expires_at or None
                    if expires_at:
                        expires_at = f"<t:{expires_at}:R>"
                    else:
                        expires_at = "попозже"
                    await message.reply(f"К сожалению у тебя закончился лимит ежедневных запросов! Попробуй {expires_at}!\n-# Забусти сервер или стань **Котик+**, чтобы иметь неограниченные запросы!")
                    return
                if message.channel.id not in [Channels.for_bots, Channels.secret]:
                    await message.reply(f"*Общение с Робокотиком доступно только в <#{Channels.for_bots}>*", delete_after=5)

                messages = [message]
                current_msg = message
                
                while len(messages) < 5 and current_msg.reference:
                    try:
                        prev_msg = current_msg.reference.resolved
                        if prev_msg is None:
                            prev_msg = await message.channel.fetch_message(current_msg.reference.message_id)
                        
                        messages.insert(0, prev_msg)
                        
                        current_msg = prev_msg
                    except disnake.NotFound:
                        break
                
                conversation = await self._buildConverstaion(messages)

                async with message.channel.typing():
                    reply, attachment, thinking_message = await self.generateAnswer(conversation, message)
                    if thinking_message:
                        await thinking_message.delete()
                    if len(reply) > 1999:
                        answers = [reply[i:i+1999] for i in range(0, len(reply), 1999)]
                        for mes in answers:
                            await message.reply(mes)
                        if attachment:
                            await message.reply(file=attachment)
                    else:
                        await message.reply(reply, file=attachment)
                    await self._limiter(message.author)
    
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