"""Local-model smoke check with synthetic data, no Discord or private files."""
import asyncio
from io import BytesIO

from PIL import Image, ImageDraw

from trainbot.ai import AIChat
from trainbot.attachments import prepare_attachments
from trainbot.config import Settings


class Fixture:
    def __init__(self, name, data):
        self.filename, self.data, self.size = name, data, len(data)

    async def read(self):
        return self.data


async def main():
    ai = AIChat(Settings.load())
    await asyncio.to_thread(ai.initialize)
    if not ai.available:
        raise SystemExit(ai.status)
    async with prepare_attachments([Fixture("memo.txt", "会議日は10月20日です。".encode())]) as prepared:
        result = await ai.respond((0, 0, 1), "会議日はいつですか。一文で答えてください。", document_text=prepared.text)
        print("Document response:", result, flush=True)
    buffer = BytesIO()
    image = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(image).rectangle((64, 64, 192, 192), fill="red")
    image.save(buffer, "PNG")
    async with prepare_attachments([Fixture("red-square.png", buffer.getvalue())]) as prepared:
        result = await ai.respond((0, 0, 2), "画像の中央にある図形と色を説明してください。", images=prepared.images)
        print("Image response:", result, flush=True)
        path = prepared.images[0][0]
    print("Temporary image removed:", not path.exists(), flush=True)
    followup = await ai.respond((0, 0, 2), "先ほどの画像について、図形は何色でしたか。一文で答えてください。")
    print("Image follow-up after cleanup:", followup, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
