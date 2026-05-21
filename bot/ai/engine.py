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

    async def load_ai(self, bot):
        await self._loadAIData()
        await self._getNewClient()
        self.bot = bot
        print(self.current_vendor, self.current_model)
        print("[[ AI IS LOCKED AND LOADED! ]]")
        models = await self.client.models.list()
        async for model in models:
            print(model.id)

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
        image = await self.client.images.generate(
            model="gemini-2.5-flash-image",
            prompt=prompt,
            response_format='b64_json',
            n=1
        )

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
    
    async def _executeTool(self, tool_call, ctx: Context):
        function_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        match function_name:
            case "search_wiki":
                yield Status(":book: Ищу по вики...")
                try:
                    results = wiki.search(args.get("query"))
                except:
                    yield _ToolDone("😞 Поиск не удался...")
                    content = "[[ Embedding raised an error - tell user that you can't find info right now and they should try later ]]"
                else:
                    if results:
                        content = wiki.build_context(results)
                        yield _ToolDone(content=content, attachment=None)
            case "generate_image":
                if Roles.ai_cd_bypass & {r.id for r in ctx.user.roles}:
                    yield Status(":paintbrush: Создаю картинку...")
                    prompt = args.get("prompt")
                    attachment = await self._generateImage(prompt)
                    yield _ToolDone(content="[[ Image generated successfully. Tell user their image is ready. ]]", attachment=attachment)
                else:
                    yield _ToolDone(content="[[ User does not have permission to generate AI images. They can get that with buying Котик+ or boosting this server. ]]")
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
        
    async def generateAnswer(self, conversation: list, user: disnake.Member):
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
                yield AIError("*Пш-ш-ш-ш... Процессор робокотика перегрет от такого количества запросов! Попробуй поговорить с ним через минутку*")
                return
            except Exception as e:
                self.logger.exception("Ошибка нейросети: %s", e)
                yield AIError("*У Робокотика полетели гайки...*")
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
                    final_answer = answer.content
                await self._statistics(response.usage.total_tokens)
                final_answer = self.sanitize_answer(final_answer)
                yield FinalAnswer(final_answer, attachment)
                return
            
    def sanitize_answer(self, text) -> str:
        text = re.sub(r'<thought>.*?</thought>\s*', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'(?<!`)`(?!`)', r'\\`', text)
        dog = re.compile(
            r'(?<=<)@'
            r'|(?<!\w)@(?=\w)',
        )
        text = dog.sub('🐶', text)
        return text