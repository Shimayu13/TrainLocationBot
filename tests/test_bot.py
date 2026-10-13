import asyncio
from dataclasses import replace
import importlib
from pathlib import Path
import unittest
import os
from unittest.mock import AsyncMock, Mock, patch

from trainbot.ai import AIChat, AIError
from trainbot.bot import create_bot
from trainbot.catalog import LEGACY_LINES, TOBU_LINES, OPERATORS, operator_for
from trainbot.config import Settings
from trainbot.messages import split_messages
from trainbot.odpt import ODPTClient, ODPTError, odpt_get
from trainbot.services import RailwayService


def settings():
    return Settings("fake-discord", "challenge-secret", "standard-secret", "https://challenge.invalid/v4", "https://standard.invalid/v4", "https://public.invalid/v4", True, 1, 2, 2, 60, 2, 100)


class TransportTests(unittest.TestCase):
    def test_routes_and_key_isolation(self):
        client = ODPTClient(settings())
        session = Mock()
        session.get.return_value.json.return_value = []
        with patch.object(client, "_session", return_value=session):
            for operator, endpoint, key in [("Toei", "public", None), ("JR-East", "challenge", "challenge-secret"), ("jre-is", "challenge", "challenge-secret"), ("Tobu", "challenge", "challenge-secret"), ("YokohamaMunicipal", "standard", "standard-secret")]:
                client.fetch("Train", operator)
                args, kwargs = session.get.call_args
                self.assertEqual(args[0], f"https://{endpoint}.invalid/v4/odpt:Train")
                self.assertEqual(kwargs["params"].get("acl:consumerKey"), key)
                self.assertEqual(kwargs["timeout"], (5, 15))

    def test_cache_returns_independent_data(self):
        client = ODPTClient(settings())
        session = Mock()
        session.get.return_value.json.return_value = [{"value": 1}]
        with patch.object(client, "_session", return_value=session):
            client.fetch("Railway", "Tobu")[0]["value"] = 2
            self.assertEqual(client.fetch("Railway", "Tobu"), [{"value": 1}])
            self.assertEqual(session.get.call_count, 1)

    def test_errors_do_not_leak_keys(self):
        import requests
        client = ODPTClient(settings())
        session = Mock()
        session.get.side_effect = requests.ConnectionError("https://example/?acl:consumerKey=challenge-secret")
        with patch.object(client, "_session", return_value=session):
            with self.assertRaises(ODPTError) as caught:
                client.fetch("Train", "Tobu")
            self.assertNotIn("secret", str(caught.exception))

    def test_old_url_does_not_supply_authentication(self):
        client = Mock()
        with patch("trainbot.odpt.default_client", return_value=client):
            odpt_get("https://api-challenge2024.odpt.org/api/v4/odpt:Train?odpt:operator=odpt.Operator:Tobu&acl:consumerKey=old-secret")
            client.fetch.assert_called_once_with("Train", "odpt.Operator:Tobu")

    def test_missing_standard_key_does_not_reuse_challenge(self):
        with self.assertRaisesRegex(ODPTError, "ODPT_STANDARD_CONSUMER_KEY"):
            ODPTClient(replace(settings(), standard_key="")).fetch("Train", "YokohamaMunicipal")


class SettingsTests(unittest.TestCase):
    def test_ai_generation_configuration_and_invalid_values(self):
        with patch("trainbot.config.load_dotenv"), patch.dict(os.environ, {"AI_TEMPERATURE": "0.8", "AI_TOP_P": "0.95", "AI_MAX_RESPONSE_TOKENS": "1200"}, clear=True):
            config = Settings.load()
            self.assertEqual((config.ai_temperature, config.ai_top_p, config.ai_max_response_tokens), (0.8, 0.95, 1200))
        for variable, value in [("AI_TEMPERATURE", "nan"), ("AI_TEMPERATURE", "1.5"), ("AI_TOP_P", "0"), ("AI_MAX_RESPONSE_TOKENS", "-1")]:
            with patch("trainbot.config.load_dotenv"), patch.dict(os.environ, {variable: value}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.load()


class RailwayTests(unittest.TestCase):
    def test_displayed_operator_names_are_accepted(self):
        for operator in OPERATORS.values():
            self.assertEqual(operator_for(operator.name), operator)

    def test_tobu_current_train_type_ids_are_visible(self):
        from trainbot.legacy.TrainLocationTJ import fetch_train_data
        from trainbot.odpt import LegacyResponse
        fixture = [{"odpt:railway": "odpt.Railway:Tobu.Tojo", "odpt:fromStation": "odpt.Station:Tobu.Tojo.A", "odpt:trainType": "odpt.TrainType:Tobu." + kind, "odpt:railDirection": "odpt.RailDirection:Inbound"} for kind in ["Local", "SemiExpress", "Express", "RapidExpress"]]
        with patch("trainbot.legacy.TrainLocationTJ.odpt_get", return_value=LegacyResponse(fixture)):
            _, display = fetch_train_data({"A": "甲駅"}, ["甲駅"])
            self.assertEqual(display["甲駅"]["inbound"], ["△", "▲", "▲", "▲"])

    def test_sotetsu_station_without_station_code_is_included(self):
        from trainbot.legacy.TrainLocationJA_SO import fetch_station_mapping
        from trainbot.odpt import LegacyResponse
        railway = {"owl:sameAs": "odpt.Railway:JR-East.SotetsuDirect", "odpt:stationOrder": [{"odpt:station": "odpt.Station:JR-East.SotetsuDirect.HazawaYokohamaKokudai", "odpt:index": 1}]}
        station = {"odpt:railway": railway["owl:sameAs"], "owl:sameAs": railway["odpt:stationOrder"][0]["odpt:station"], "dc:title": "羽沢横浜国大", "odpt:stationCode": None}
        with patch("trainbot.legacy.TrainLocationJA_SO.odpt_get", side_effect=[LegacyResponse([railway]), LegacyResponse([station])]):
            self.assertEqual(fetch_station_mapping(), ({"HazawaYokohamaKokudai": "羽沢横浜国大"}, ["羽沢横浜国大"]))

    def test_indexed_station_modules_receive_the_index(self):
        service = RailwayService(Mock())
        for name, module_name in [("五日市線", "JC_I"), ("川越線", "JREKA"), ("宇都宮線", "JU"), ("高崎線", "JU_Taka")]:
            module = importlib.import_module("trainbot.legacy.TrainLocation" + module_name)
            with patch.object(module, "fetch_station_index_mapping", return_value={"A": 1}), patch.object(module, "fetch_station_mapping", return_value=({"A": "甲駅"}, ["甲駅"])) as mapping, patch.object(module, "fetch_train_data", return_value=(["甲駅"], {})), patch.object(module, "format_train_positions", return_value="甲駅"):
                self.assertEqual(service.positions(name), "甲駅")
                mapping.assert_called_once_with({"A": 1})

    def test_keiyo_terminus_uses_inbound_outbound_fields(self):
        from trainbot.legacy.TrainLocationJE import format_train_positions
        display = {name: {"inbound": ["△"], "outbound": ["▽"], "between": {"inbound": [], "outbound": []}} for name in ["千葉みなと", "蘇我"]}
        result = format_train_positions(["千葉みなと", "蘇我"], display)
        self.assertIn("蘇我", result)
        self.assertIn("△", result)

    def test_commands_and_legacy_imports_are_preserved(self):
        with patch("requests.Session.get", side_effect=AssertionError("network on import")):
            for path in (Path(__file__).resolve().parents[1] / "trainbot" / "legacy").glob("Train*.py"):
                module = importlib.import_module("trainbot.legacy." + path.stem)
                self.assertTrue(callable(module.main))
            bot = create_bot(settings())
            for name in [*LEGACY_LINES, *TOBU_LINES, "サイコロ", "えっち", "おみくじ", "運行情報", "AI", "AIリセット"]:
                self.assertIsNotNone(bot.get_command(name), name)

    def test_every_legacy_command_fetches_formats_and_sends(self):
        class Context:
            async def send(self, text, **kwargs):
                sent.append(text)
            def typing(self):
                class Typing:
                    async def __aenter__(self): pass
                    async def __aexit__(self, *args): pass
                return Typing()
        async def check():
            bot = create_bot(settings())
            for name in LEGACY_LINES:
                with patch.object(bot.railways, "positions", return_value="駅名\n▽ 列車") as fetch:
                    await bot.get_command(name).callback(Context())
                    fetch.assert_called_once_with(name)
                    self.assertEqual(sent.pop(), "```\n駅名\n▽ 列車\n```")
        sent = []
        asyncio.run(check())

    def test_tobu_station_order_and_between_station_train(self):
        railway = {"owl:sameAs": "odpt.Railway:Tobu.Nikko", "dc:title": "東武日光線", "odpt:stationOrder": [
            {"odpt:index": 2, "odpt:station": "station.B", "odpt:stationTitle": {"ja": "乙駅"}},
            {"odpt:index": 1, "odpt:station": "station.A", "odpt:stationTitle": {"ja": "甲駅"}}]}
        client = Mock()
        client.fetch.side_effect = lambda kind, operator: {"Railway": [railway], "Station": [], "Train": [
            {"odpt:railway": railway["owl:sameAs"], "odpt:fromStation": "station.A", "odpt:toStation": "station.B", "odpt:trainNumber": "123"}]}[kind]
        result = RailwayService(client).positions("東武日光線")
        self.assertLess(result.index("01 甲駅"), result.index("02 乙駅"))
        self.assertIn("123 | 甲駅 → 乙駅", result)

    def test_information_text_survives_missing_status(self):
        client = Mock()
        client.fetch.side_effect = lambda kind, operator: [] if kind == "Railway" else [{"odpt:trainInformationStatus": None, "odpt:trainInformationText": {"ja": "遅延しています"}}]
        self.assertIn("遅延しています", RailwayService(client).information("JR"))

    def test_normal_routes_are_omitted_unless_a_line_is_requested(self):
        normal = {"odpt:railway": "odpt.Railway:JR-East.Tokaido", "odpt:trainInformationText": {"ja": "平常運転しています。"}}
        delayed = {"odpt:railway": "odpt.Railway:JR-East.Yamanote", "odpt:trainInformationText": {"ja": "遅延しています。"}}
        client = Mock()
        client.fetch.side_effect = lambda kind, operator: [] if kind == "Railway" else [normal, delayed]
        service = RailwayService(client)
        result = service.information("JR")
        self.assertNotIn("平常運転", result)
        self.assertIn("遅延しています", result)
        for query in ["東海道線", "JR 東海道線", "JR-East.Tokaido"]:
            result = service.information(query)
            self.assertIn("平常運転しています", result)
            self.assertNotIn("遅延しています", result)

    def test_all_normal_is_silent_but_missing_information_is_not(self):
        client = Mock()
        client.fetch.side_effect = lambda kind, operator: [] if kind == "Railway" else [{"odpt:trainInformationText": {"ja": "平常運転"}}]
        service = RailwayService(client)
        self.assertIsNone(service.information("JR"))
        client.fetch.return_value = []
        client.fetch.side_effect = None
        self.assertIn("公開されている運行情報はありません", service.information("JR"))

    def test_normal_status_does_not_hide_disruption_details(self):
        from trainbot.services import is_normal_information
        self.assertFalse(is_normal_information({"odpt:trainInformationStatus": {"ja": "平常運転"}, "odpt:trainInformationText": {"ja": "現在平常運転ですが、一部列車に遅れがあります。"}}))


    def test_yokohama_renderers_with_api_fixture(self):
        from trainbot.odpt import LegacyResponse
        for suffix, railway, code in [("BL", "Blue", "B"), ("GL", "Green", "G")]:
            module = importlib.import_module("trainbot.legacy.TrainLocation" + suffix)
            stations = [{"odpt:railway": "odpt.Railway:YokohamaMunicipal." + railway, "odpt:stationCode": code + "01", "owl:sameAs": "odpt.Station:YokohamaMunicipal." + railway + ".A", "dc:title": "甲駅"}]
            trains = [{"odpt:railway": "odpt.Railway:YokohamaMunicipal." + railway, "odpt:fromStation": stations[0]["owl:sameAs"], "odpt:trainType": "odpt.TrainType:YokohamaMunicipal.Local", "odpt:railDirection": "odpt.RailDirection:Inbound"}]
            with patch.object(module, "odpt_get", side_effect=[LegacyResponse(stations), LegacyResponse(trains)]):
                result = RailwayService(Mock()).positions("ブルーライン" if suffix == "BL" else "グリーンライン")
                self.assertIn("甲駅", result)
                self.assertIn("△", result)

    def test_long_messages_account_for_fences(self):
        text = "駅名\n" * 2000 + "長" * 4000
        chunks = split_messages(text, code=True)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))
        self.assertEqual("".join(chunk[4:-4] for chunk in chunks), text)


class FakeSession:
    def __init__(self, **kwargs):
        self.prompts = []
        self.instructions = kwargs.get("instructions")
        self.options = []
    async def respond(self, prompt, *, options=None):
        self.prompts.append(prompt)
        self.options.append(options)
        await asyncio.sleep(0)
        return "応答"


class FakeSDK:
    class SystemLanguageModel:
        def is_available(self): return True, None
    LanguageModelSession = FakeSession
    GenerationOptions = staticmethod(lambda **kwargs: kwargs)
    class SamplingMode:
        random = staticmethod(lambda **kwargs: kwargs)


class BotMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_command_selects_reports_and_reaches_ai_reply(self):
        bot = create_bot(settings())
        bot.ai = AIChat(settings(), FakeSDK)
        bot.ai.initialize()
        ctx = Mock(guild=Mock(id=1), channel=Mock(id=2), author=Mock(id=3), send=AsyncMock())
        class Typing:
            async def __aenter__(self): pass
            async def __aexit__(self, *args): pass
        ctx.typing.return_value = Typing()
        await bot.get_command("AIモード").callback(ctx, mode="Menima AI Lite")
        self.assertIn("Menima AI Lite", ctx.send.await_args.args[0])
        await bot.get_command("AIモード").callback(ctx)
        self.assertIn("現在のモード: Menima AI Lite", ctx.send.await_args.args[0])
        await bot.reply_ai(ctx, "質問")
        self.assertEqual(bot.ai.sessions[(1, 2, 3)].mode, "lite")

    async def test_mention_uses_trailing_message_without_invoking_command(self):
        bot = create_bot(settings())
        bot._connection.user = Mock(id=100)
        ctx = Mock()
        with patch.object(bot, "get_context", new=AsyncMock(return_value=ctx)), patch.object(bot, "reply_ai", new=AsyncMock()) as reply, patch.object(bot, "invoke", new=AsyncMock()) as invoke:
            for content, prompt in [("<@100> こんにちは", "こんにちは"), ("<@!100>\n文章を考えて\n短めに", "文章を考えて\n短めに"), ("質問です <@100> こんにちは <@100>", "こんにちは")]:
                await bot.on_message(Mock(author=Mock(id=200, bot=False), content=content))
                reply.assert_awaited_with(ctx, prompt)
            invoke.assert_not_awaited()

    async def test_bare_mention_gives_help_and_ai_error_is_reported(self):
        bot = create_bot(settings())
        bot._connection.user = Mock(id=100)
        ctx = Mock(send=AsyncMock())
        with patch.object(bot, "get_context", new=AsyncMock(return_value=ctx)), patch.object(bot, "reply_ai", new=AsyncMock(side_effect=AIError("モデル未準備"))) as reply:
            await bot.on_message(Mock(author=Mock(id=200), content="<@100> "))
            reply.assert_not_awaited()
            self.assertIn("質問", ctx.send.await_args.args[0])
            await bot.on_message(Mock(author=Mock(id=200), content="<@100> 質問"))
            self.assertEqual(ctx.send.await_args.args[0], "モデル未準備")

    async def test_other_mentions_do_not_trigger_ai_and_own_mentions_are_ignored(self):
        bot = create_bot(settings())
        bot._connection.user = Mock(id=100)
        with patch.object(bot, "get_context", new=AsyncMock()), patch.object(bot, "reply_ai", new=AsyncMock()) as reply, patch.object(bot, "invoke", new=AsyncMock()) as invoke:
            for content in ["<@200> こんにちは", "<@&100> こんにちは", "@everyone こんにちは"]:
                await bot.on_message(Mock(author=Mock(id=200), content=content))
            self.assertEqual(invoke.await_count, 3)
            await bot.on_message(Mock(author=Mock(id=100), content="<@100> 自分の返信"))
            reply.assert_not_awaited()
            self.assertEqual(invoke.await_count, 3)

    async def test_all_normal_result_sends_no_reply(self):
        bot = create_bot(settings())
        ctx = Mock(send=AsyncMock())
        class Typing:
            async def __aenter__(self): pass
            async def __aexit__(self, *args): pass
        ctx.typing.return_value = Typing()
        with patch.object(bot.railways, "run", new=AsyncMock(return_value=None)):
            await bot.get_command("運行情報").callback(ctx, operator="JR")
        ctx.send.assert_not_awaited()

    async def test_other_bot_and_human_commands_are_processed(self):
        bot = create_bot(settings())
        bot._connection.user = Mock(id=100)
        context = Mock()
        with patch.object(bot, "get_context", new=AsyncMock(return_value=context)) as parse, patch.object(bot, "invoke", new=AsyncMock()) as invoke:
            for is_bot in [True, False]:
                message = Mock(author=Mock(id=200, bot=is_bot))
                await bot.on_message(message)
                parse.assert_awaited_with(message)
                invoke.assert_awaited_with(context)
            self.assertEqual(invoke.await_count, 2)

    async def test_own_messages_are_ignored(self):
        bot = create_bot(settings())
        bot._connection.user = Mock(id=100)
        with patch.object(bot, "get_context", new=AsyncMock()) as parse, patch.object(bot, "invoke", new=AsyncMock()) as invoke:
            await bot.on_message(Mock(author=Mock(id=100, bot=True)))
            parse.assert_not_awaited()
            invoke.assert_not_awaited()


class AITests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_switching_preserves_history_and_isolated_by_user_channel(self):
        ai = AIChat(settings(), FakeSDK)
        ai.initialize()
        key = (1, 1, 1)
        self.assertEqual(ai.mode_for(key), "plus")
        await ai.respond(key, "質問")
        self.assertIn("500〜1200", ai.sessions[key].session.prompts[-1])
        original = ai.sessions[key].session
        await ai.set_mode(key, "Menima AI Lite")
        self.assertIs(ai.sessions[key].session, original)
        await ai.respond(key, "質問")
        self.assertIn("80〜180", ai.sessions[key].session.prompts[-1])
        self.assertEqual(ai.mode_for((1, 1, 2)), "plus")
        self.assertEqual(ai.mode_for((1, 2, 1)), "plus")
        session = ai.sessions[key].session
        await ai.set_mode(key, "Lite")
        self.assertIs(ai.sessions[key].session, session)
        await ai.reset(key)
        self.assertEqual(ai.mode_for(key), "lite")
        with self.assertRaises(AIError):
            await ai.set_mode(key, "invalid")
        self.assertEqual(ai.mode_for(key), "lite")

    async def test_mode_preferences_are_bounded_and_flash_is_distinct(self):
        ai = AIChat(settings(), FakeSDK)
        ai.initialize()
        await ai.set_mode((1, 1, 1), "Lite")
        await ai.set_mode((1, 1, 2), "フラッシュ")
        await ai.respond((1, 1, 2), "質問")
        self.assertIn("200〜450", ai.sessions[(1, 1, 2)].session.prompts[-1])
        await ai.set_mode((1, 1, 3), "Plus")
        self.assertEqual(len(ai.modes), 2)
        self.assertEqual(ai.mode_for((1, 1, 1)), "plus")

    async def test_flexible_instructions_and_configurable_generation(self):
        config = replace(settings(), ai_temperature=0.8, ai_top_p=0.95, ai_max_response_tokens=1200)
        ai = AIChat(config, FakeSDK)
        ai.initialize()
        await ai.respond((1, 1, 1), "詳しく教えて")
        session = ai.sessions[(1, 1, 1)].session
        self.assertIn("利用者の長さや形式の指定を優先", session.instructions)
        self.assertNotIn("Core 3", session.instructions)
        self.assertTrue(session.prompts[0].endswith("利用者の質問:\n詳しく教えて"))
        self.assertEqual(session.options[0], {"temperature": 0.8, "sampling": {"probability_threshold": 0.95}, "maximum_response_tokens": 1200})

    async def test_default_generation_does_not_impose_a_short_output_cap(self):
        ai = AIChat(settings(), FakeSDK)
        ai.initialize()
        await ai.respond((1, 1, 1), "こんにちは")
        self.assertIsNone(ai.sessions[(1, 1, 1)].session.options[0]["maximum_response_tokens"])

    async def test_isolation_reset_and_bounded_history(self):
        ai = AIChat(settings(), FakeSDK)
        ai.initialize()
        await ai.respond((1, 1, 1), "甲")
        first = ai.sessions[(1, 1, 1)].session
        await ai.respond((1, 1, 2), "乙")
        self.assertIsNot(first, ai.sessions[(1, 1, 2)].session)
        await ai.respond((1, 1, 1), "丙")
        await ai.respond((1, 1, 1), "丁")
        self.assertIs(first, ai.sessions[(1, 1, 1)].session)
        ai.sessions[(1, 1, 1)].touched = 0
        await ai.respond((1, 1, 1), "戊")
        self.assertIs(first, ai.sessions[(1, 1, 1)].session)
        with self.assertRaisesRegex(AIError, "会話保持件数"):
            await ai.respond((1, 1, 3), "第三の利用者")
        await ai.reset((1, 1, 1))
        self.assertNotIn((1, 1, 1), ai.sessions)

    async def test_sdk_failure_leaves_railway_bot_available(self):
        ai = AIChat(settings(), FakeSDK)
        with self.assertRaises(AIError):
            await ai.respond((1, 1, 1), "質問")

    async def test_limit_and_timeout_preserve_session(self):
        ai = AIChat(settings(), FakeSDK)
        ai.initialize()
        with self.assertRaises(AIError):
            await ai.respond((1, 1, 1), "長" * 101)
        async def slow(prompt, **kwargs):
            await asyncio.sleep(10)
        ai.sessions[(1, 1, 1)] = __import__("trainbot.ai", fromlist=["Conversation"]).Conversation(Mock(respond=slow), __import__("time").monotonic())
        with self.assertRaisesRegex(AIError, "時間切れ"):
            await ai.respond((1, 1, 1), "質問")
        self.assertEqual(ai.pending, 0)
        self.assertIn((1, 1, 1), ai.sessions)



class ContextRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_overflow_keeps_session(self):
        ai = AIChat(settings(), FakeSDK)
        ai.initialize()
        key = (1, 2, 3)
        await ai.respond(key, "質問")
        session = ai.sessions[key].session
        error = type("ExceededContextWindowSizeError", (Exception,), {})
        with patch.object(session, "respond", new=AsyncMock(side_effect=error())):
            with self.assertRaisesRegex(AIError, "AIリセット"):
                await ai.respond(key, "続けて")
        self.assertIs(ai.sessions[key].session, session)
        await ai.reset(key)
        self.assertNotIn(key, ai.sessions)

    async def test_explicit_clear_is_scoped_to_requesting_user(self):
        bot = create_bot(settings())
        bot.ai = AIChat(settings(), FakeSDK)
        bot.ai.initialize()
        ctx = Mock(guild=Mock(id=1), channel=Mock(id=2), author=Mock(id=3), send=AsyncMock())
        key = (1, 2, 3)
        other = (1, 2, 4)
        await bot.ai.set_mode(key, "Lite")
        await bot.ai.respond(key, "質問")
        await bot.ai.respond(other, "別の利用者")
        await bot.reply_ai(ctx, "コンテキストを空にしてください")
        self.assertNotIn(key, bot.ai.sessions)
        self.assertIn(other, bot.ai.sessions)
        self.assertEqual(bot.ai.mode_for(key), "lite")
        self.assertIn("消去しました", ctx.send.await_args.args[0])

if __name__ == "__main__":
    unittest.main()
