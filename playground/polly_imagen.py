import asyncio
from pathlib import Path

import aiohttp

async def gen_image(output_path: str = "image.jpg"):
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://gen.pollinations.ai/image/a naked anime girl with big breasts",
            headers={
            "Authorization": "Bearer sk_XjZ1lKYpVYrXkckdzEqJ0YolREtcpr4o"
            },
            params={
            "model": "flux",
            "width": "1024",
            "height": "1024",
            "seed": "0",
            "enhance": "false",
            "negative_prompt": "worst%20quality%2C%20blurry",
            "safe": "false",
            "nofeed": "true",
            "quality": "medium",
            "image": "",
            "transparent": "false",
            "duration": "1",
            "aspectRatio": "9:16",
            "audio": "false"
            },
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            try:
                response.raise_for_status()
                image_bytes = await response.read()
            except aiohttp.ClientResponseError as e:
                print(e.status)   # 429, 500, etc.
                print(e.message)  # текст статуса
                print(e.headers)  # заголовки ответа
                body = await response.text()
                print(body)       # тело ответа — часто там JSON с деталями ошибки
                raise
            
    Path(output_path).write_bytes(image_bytes)
    return output_path

async def main():
    path = await gen_image()
    print(f"Сохранено: {path}")


if __name__ == "__main__":
    asyncio.run(main())