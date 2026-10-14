from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zipfile import ZipFile

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from trainbot.ai import AIChat, AIError
from trainbot.attachments import extract_text, prepare_attachments
from trainbot.bot import create_bot
from test_bot import FakeSDK, settings


def attachment(name, data):
    return Mock(filename=name, size=len(data), read=AsyncMock(return_value=data))


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_and_text_are_prepared_and_temp_file_removed(self):
        buffer = BytesIO()
        Image.new("RGB", (1200, 600), "red").save(buffer, "PNG")
        files = [attachment("../../photo.png", buffer.getvalue()), attachment("memo.txt", "会議は10月20日です。".encode())]
        async with prepare_attachments(files) as result:
            path = result.images[0][0]
            self.assertTrue(path.exists())
            self.assertIn("会議は10月20日", result.text)
            with Image.open(path) as image:
                self.assertEqual(image.size, (1024, 512))
        self.assertFalse(path.exists())

    async def test_rejected_files_are_not_downloaded(self):
        for files in [[attachment("file.exe", b"binary")], [attachment("a.txt", b"a")] * 4, [Mock(filename="big.txt", size=5 * 1024 * 1024, read=AsyncMock())]]:
            with self.assertRaises(AIError):
                async with prepare_attachments(files):
                    pass
            for file in files:
                file.read.assert_not_awaited()

    async def test_long_text_reports_partial_input(self):
        async with prepare_attachments([attachment("long.txt", ("長" * 3000).encode())]) as result:
            self.assertEqual(result.text.count("長"), 2000)
            self.assertTrue(result.notices)

    async def test_docx_text_and_empty_pdf(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>テスト本文</w:t></w:r></w:p></w:body></w:document>')
        text, truncated = extract_text(buffer.getvalue(), ".docx", 2000)
        self.assertEqual(text, "テスト本文")
        self.assertFalse(truncated)
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        pdf = BytesIO()
        writer.write(pdf)
        with self.assertRaisesRegex(AIError, "文字を抽出"):
            extract_text(pdf.getvalue(), ".pdf", 2000)

    async def test_pdf_text_extraction_and_cleanup_on_model_failure(self):
        writer = PdfWriter()
        page = writer.add_blank_page(200, 100)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 50 Td (Meeting on October 20.) Tj ET")
        page[NameObject("/Contents")] = stream
        output = BytesIO()
        writer.write(output)
        text, truncated = extract_text(output.getvalue(), ".pdf", 2000)
        self.assertIn("Meeting on October 20", text)
        self.assertFalse(truncated)
        image = BytesIO()
        Image.new("RGB", (10, 10), "red").save(image, "PNG")
        with self.assertRaises(RuntimeError):
            async with prepare_attachments([attachment("photo.png", image.getvalue())]) as result:
                path = result.images[0][0]
                raise RuntimeError("model failure")
        self.assertFalse(path.exists())

    async def test_ai_receives_multimodal_prompt_and_document(self):
        class SDK(FakeSDK):
            ImageAttachment = staticmethod(lambda path, label=None: (path, label))
        ai = AIChat(settings(), SDK)
        ai.initialize()
        await ai.respond((1, 2, 3), "添付を説明", images=[(Path("fixture.png"), "photo")], document_text="添付本文です")
        prompt = ai.sessions[(1, 2, 3)].session.prompts[-1]
        self.assertIsInstance(prompt, list)
        self.assertIn("添付本文です", prompt[0])
        self.assertEqual(prompt[1], (Path("fixture.png"), "photo"))

    async def test_ai_command_with_attachment_and_no_question(self):
        bot = create_bot(settings())
        bot.ai.available = True
        ctx = Mock(guild=Mock(id=1), channel=Mock(id=2), author=Mock(id=3), send=AsyncMock(), message=Mock(attachments=[attachment("memo.txt", b"hello")]))
        class Typing:
            async def __aenter__(self): pass
            async def __aexit__(self, *args): pass
        ctx.typing.return_value = Typing()
        with patch.object(bot.ai, "respond", new=AsyncMock(return_value="説明しました")) as respond:
            await bot.get_command("AI").callback(ctx)
            self.assertIn("hello", respond.await_args.kwargs["document_text"])
            self.assertEqual(respond.await_args.args[1], "添付の内容を説明してください。")
        self.assertEqual(ctx.send.await_args.args[0], "説明しました")

    async def test_bare_bot_mention_with_attachment_calls_ai(self):
        bot = create_bot(settings())
        bot._connection.user = Mock(id=100)
        message = Mock(author=Mock(id=200), content="<@100>", attachments=[attachment("memo.txt", b"hello")])
        ctx = Mock()
        with patch.object(bot, "get_context", new=AsyncMock(return_value=ctx)), patch.object(bot, "reply_ai", new=AsyncMock()) as reply:
            await bot.on_message(message)
            reply.assert_awaited_once_with(ctx, "")

