import base64
from datetime import datetime, timedelta, timezone
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

from dataclasses import dataclass, field

from bot.storage import Channels, Roles

load_dotenv()

@dataclass
class Status:
    content: str

@dataclass
class _ToolDone:
    content: str
    attachment: disnake.File | None = None

@dataclass
class FinalAnswer:
    content: str
    attachments: list[disnake.File] = field(default_factory=list)

@dataclass
class AIError:
    content: str

@dataclass
class Context:
    user: disnake.Member = None

class AIEngine(commands.Cog):
    """ 
    AI SaaS LLM Neuro Agent Assistant 
    """

    def __init__(self):
        self.logger = logging.getLogger("robocat.ai")
        
        
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
            {
                "type": "function",
                "function": {
                    "name": "user_info",
                "description": "Get current's user discord info (right now - only their roles)"
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
                            "description": "Duration of mute in seconds. Max duration - 1209600s (14 days)"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason of mute. Include duration of mute."
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
        self.ai_locked_bypass_user_ids = [531208170098655233] # Чтоэто? Я не помню

        # AI Info
        self.max_tokens = 1024
        self.has_vision: bool = False # Есть ли у текущей модели просмотр картинок юзера
        self.thinking = None # None, low, medium, high
        self.temperature = 0.6
        self.top_p = 1

        self.flags = flags

    async def load_ai(self, bot):
        await self._loadAIData()
        await self._getNewClient()
        self.bot = bot
        print(self.current_vendor, self.current_model)
        print("[[ AI IS LOCKED AND LOADED! ]]")

    async def _loadAIData(self):
        VENDORS_PATH = Path(__file__).resolve().parents[2] / "data" / "ai_settings.yaml"
        with VENDORS_PATH.open("r", encoding='utf-8') as file:
            data = yaml.safe_load(file)
            self.system_prompt = data["system_prompt"]
            self.vendors = data["vendors"]

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
    
    async def _base64Image(self, attach: disnake.Attachment):
        image = await attach.read()
        base64_image = base64.b64encode(image).decode('utf-8')
        return f"data:image/jpeg;base64,{base64_image}"
    
    async def _generateImage(self, prompt: str):
        try:
            image = await self.client.images.generate(
                model="gemini-2.5-flash-image",
                prompt=prompt,
                response_format='b64_json',
                n=1
            )
        except:
            return None
        else:
            files = []
            for i, image_data in enumerate(image.data):
                image = Image.open(BytesIO(base64.b64decode(image_data.b64_json)))

                buf = BytesIO()
                image.save(buf, format="PNG")
                buf.seek(0) 

                files.append(disnake.File(buf, filename=f"image_{i}.png"))
            return files
    
    async def _buildFinalMessage(text: str) -> str:
        return
    
    async def buildConverstaion(self, messages: list[disnake.Message]) -> list[dict]:
        conversation = [{
            "role": "system",
            "content": self.system_prompt.format(datetime.now().date()) or "You're helpful assistant."
        }]
        for mes in messages:
            index = 0
            role = ""
            content = ""
            match mes.author:
                case self.bot.user:
                    role = "assistant"
                    content = mes.clean_content
                    if mes.attachments:
                        attach_type = mes.attachments[0].content_type
                        content += f"[[ This message contained {attach_type} content, now it's not available ]]"
                    if mes.components:
                        try:
                            content = mes.components[0].content
                        except:
                            content = "[[ Message could not be loaded. ]]"
                    if content.count("-# cut") > 0:
                        content = "[[ This message was cutted out due to Discord message length limit, but the answer was full. ]]"
                        content = content.replace("-# cut", "")
                    conversation.append({
                        "role": role,
                        "content": content
                    })
                case _:
                    role = "user"
                    content = f"({mes.author.display_name})" + mes.clean_content
                    if mes.attachments and index == 0: # Если это текущее сообщение пользователя - обрабатываем в нём картинку (если есть офк)
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
                            index += 1
                        else:
                            content += "[[ User provided attachment that you can't process. Tell them about that politely.]]"
                    elif mes.attachments: # Не загружаем в память картинки из всего диалога - дорого по токенам!!!!!!!!1
                        content += f"[[ This message contained {mes.attachments[0].content_type} content, now it's not available ]]"
                    conversation.append({ # Так или иначе добавляем в разговор сообщение юзера
                        "role": role,
                        "content": content
                    })
        return conversation
    
    async def _executeTool(self, tool_call, ctx: Context):
        function_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        match function_name:
            case "search_wiki":
                yield Status(":book: Ищу по вики...")
                try:
                    results = wiki.search(args.get("query"))
                except:
                    yield Status("😞 Поиск не удался...")
                    yield _ToolDone(content="[[ Wiki search failed. Tell user that something went wrong and they should try again later. ]]")
                else:
                    if results:
                        content = wiki.build_context(results)
                        yield _ToolDone(content=content, attachment=None)
            case "generate_image":
                if Roles.premium_ai & {r.id for r in ctx.user.roles}:
                    image_gen_flag = await self.flags.getFlag(ctx.user, "image_gen")
                    if image_gen_flag and int(image_gen_flag.value) == 1:
                        yield _ToolDone(content=f"[[ User has already generated an image today. Tell them they can generate more in <t:{image_gen_flag.expires_at}:R>. ]]")
                        return
                    yield Status(":paintbrush: Создаю картинку...")
                    prompt = args.get("prompt")
                    attachment = await self._generateImage(prompt)
                    if attachment:
                        yield FinalAnswer(content="Твоя картиночка готова!", attachments=attachment)
                        if image_gen_flag:
                            await self.flags.setFlag(ctx.user, "image_gen", value="+1")
                        else:
                            await self.flags.setFlag(ctx.user, "image_gen", value="1", expires_at="1д")
                    else:
                        yield _ToolDone(content="[[ Image generation failed. Tell user that something went wrong and they should try again later. ]]")
                else:
                    yield _ToolDone(content="[[ User does not have permission to generate AI images. They can get that by buying Котик+ or boosting this server. ]]")
            case "user_info":
                yield Status("🏀 Смотрю твои роли... ахахах причём тут баскетбольный мяч?!")
                if ctx.user:
                    content = [i.name for i in ctx.user.roles]
                    yield _ToolDone(content=f"[[ User's roles: {content} ]]")
                else:
                    yield _ToolDone("[[ User was not provided lol ]]")
            case "mute_user":
                duration = args.get("duration")
                reason = args.get("reason")
                if ctx.user:
                    try:
                        await ctx.user.timeout(duration=duration, reason=reason)
                    except disnake.Forbidden as e:
                        print(e)
                        yield _ToolDone("[[ You can't mute this user - they are admin or have mute bypass ]]")
                    except Exception as e:
                        yield _ToolDone(f"[[ You can't mute this user - {e}. Don't tell user this error, play it cool. ]]")
                    else:
                        yield _ToolDone("[[ User is muted succesfully ]]")
            case _:
                yield _ToolDone("[[ Unknown tool called ]]")
        
    async def generateAnswer(self, 
            conversation: list, 
            user: disnake.Member):
        if not self.client:
            await self._getNewClient()
        if self.ai_locked:
            yield AIError("*Робокотик на сегодня всё... Поговори с ним попозже.")
            return
        api_params = {
            "model": self.current_model, 
            "messages": conversation,
            "temperature": self.temperature or 0.5,
            "top_p": self.top_p or 1,
            "stream": False,
            "max_tokens": self.max_tokens or 2048,
            "tools": self.tools or None
        }
        attempts = 0
        tool_rounds = 0
        attachment = None
        while attempts < 3 and tool_rounds < 2:
            try:
                response = await self.client.chat.completions.create(**api_params)
            except openai.AuthenticationError as e:
                print("================== API KEY ERROR ==================")
                self.logger.exception("Слетел какой-то API: %s", e)
                # self.vendors.pop(0)
                # await self._getNewClient()
                yield AIError("*У Робокотика слетели гайки...*")
                return
            except openai.InternalServerError as e:
                self.logger.exception("Internal server error: %s", e)
                attempts += 1
                yield Status("😞"*attempts + " *Долго думаю...*")
            except openai.RateLimitError as e:
                print("===================== RATE LIMIT =====================")
                self.logger.exception("Rate Limit: %s", e)
                yield AIError(f"*Пш-ш-ш-ш... Процессор робокотика перегрелсяи! Попробуй поговорить с ним через <t:{int(datetime.now(timezone.utc).timestamp() + 60)}:R>*")
                return
            except Exception as e:
                self.logger.exception("Ошибка нейросети: %s", e)
                yield AIError("😞 *У Робокотика полетели гайки...*")
                return
            else:
                # print(f""" 
                # Answer: {response.choices[0].message.content[:100]},
                # Stop Reason: {response.choices[0].finish_reason}
                # Usage: {response.usage.total_tokens},
                # Model: {response.model}
                # """) 
                answer = response.choices[0].message
                # print(f"prompt={response.usage.prompt_tokens} "
                # f"completion={response.usage.completion_tokens} "
                # f"total={response.usage.total_tokens}\n\n"
                # f"{response.choices[0].message}")
                conversation.append(answer.model_dump(exclude_none=True))
                bot_thinking_message = None
                if answer.tool_calls:
                    for tc in answer.tool_calls:
                        result = None
                        async for event in self._executeTool(tc, Context(user)):
                            if isinstance(event, _ToolDone):
                                result = event
                            elif isinstance(event, FinalAnswer):
                                yield event
                                return
                            else:
                                yield event
                        if result and result.attachment:
                            attachment = result.attachment
                        conversation.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": str(result.content)
                        })
                    yield Status("🤤 Ещё чуть-чуть думаю...")
                    tool_rounds += 1
                    continue
                else:
                    final_answer = answer.content or " "
                await self._statistics(response.usage.total_tokens)
                final_answer = self.sanitize_answer(final_answer)
                yield FinalAnswer(final_answer, attachment)
                return
        yield FinalAnswer("😞 *Слетели гайки... Попробуй спросить меня ещё раз.*")
            
    def sanitize_answer(self, text) -> str:
        text = re.sub(r'<thought>.*?</thought>\s*', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'(?<!`)`(?!`)', r'\\`', text)
        dog = re.compile(
            r'(?<=<)@'
            r'|(?<!\w)@(?=\w)',
        )
        text = dog.sub('🐶', text)
        return text