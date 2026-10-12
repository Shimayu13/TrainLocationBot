import asyncio
import importlib
import re

from .catalog import INFORMATION_LINES, LEGACY_LINES, TOBU_LINES, operator_for
from .odpt import ODPTError, default_client


def japanese(value, fallback=""):
    if isinstance(value, dict):
        return value.get("ja") or value.get("en") or fallback
    return value if isinstance(value, str) and value else fallback


def is_normal_information(info):
    def normalize(text):
        return re.sub(r"[\s。、.!！]", "", text).lower()
    normal = {"平常運転", "平常運行", "通常運転", "通常運行", "平常どおり運転しています", "平常通り運転しています",
              "平常運転しています", "平常運行しています", "通常運転しています", "normalservice", "normaloperation"}
    normal |= {"現在" + text for text in normal if not text.isascii()}
    status = normalize(japanese(info.get("odpt:trainInformationStatus")))
    content = normalize(japanese(info.get("odpt:trainInformationText")))
    # A normal status must not hide explanatory text about a disruption.
    return (not status or status in normal) and (content in normal or not content and status in normal)


class RailwayService:
    def __init__(self, client=None):
        self.client = client or default_client()
        self._slots = asyncio.Semaphore(4)

    async def run(self, function, *args):
        async with self._slots:
            return await asyncio.to_thread(function, *args)

    def legacy_positions(self, name):
        module = importlib.import_module("trainbot.legacy.TrainLocation" + LEGACY_LINES[name])
        if hasattr(module, "fetch_station_index_mapping"):
            mapping, names = module.fetch_station_mapping(module.fetch_station_index_mapping())
        else:
            mapping, names = module.fetch_station_mapping()
        if not names:
            raise ODPTError("駅情報が公開されていないか、対象路線の駅を取得できませんでした。")
        stations, display = module.fetch_train_data(mapping, names)
        return module.format_train_positions(stations, display)

    def positions(self, name):
        if name in LEGACY_LINES:
            return self.legacy_positions(name)
        if name in TOBU_LINES:
            return self.generic_positions("Tobu", TOBU_LINES[name])
        # Also accepts the exact operator.line identifier returned by the API.
        if "." in name and name.split(".", 1)[0] in {"JR-East", "Toei", "Tobu", "YokohamaMunicipal", "Keikyu"}:
            return self.generic_positions(*name.split(".", 1))
        raise ODPTError("路線が見つかりません。「対応路線」を確認してください。")

    def generic_positions(self, operator, line):
        railway_id = f"odpt.Railway:{operator}.{line}"
        railways = self.client.fetch("Railway", operator)
        railway = next((r for r in railways if r.get("owl:sameAs") == railway_id), None)
        if railway is None:
            raise ODPTError("対象路線の駅順情報がAPIで公開されていません。")
        stations = self.client.fetch("Station", operator)
        titles = {s.get("owl:sameAs"): japanese(s.get("odpt:stationTitle"), japanese(s.get("dc:title"))) for s in stations}
        order = sorted(railway.get("odpt:stationOrder") or [], key=lambda s: s.get("odpt:index", 0))
        if not order:
            raise ODPTError("対象路線の駅順情報が空です。")
        labels = {s.get("odpt:station"): japanese(s.get("odpt:stationTitle"), titles.get(s.get("odpt:station")) or str(s.get("odpt:station", "不明"))) for s in order}
        trains = [t for t in self.client.fetch("Train", operator) if t.get("odpt:railway") == railway_id]
        lines = [japanese(railway.get("odpt:railwayTitle"), japanese(railway.get("dc:title"), railway_id))]
        lines.extend(f"{i + 1:02d} {labels[s.get('odpt:station')]}" for i, s in enumerate(order))
        lines.append("\n列車位置（公開データ）")
        if not trains:
            lines.append("現在、公開されている列車はありません。")
        for train in trains:
            def station_label(value):
                return labels.get(value) or titles.get(value) or str(value or "位置不明")
            origin, destination = train.get("odpt:fromStation"), train.get("odpt:toStation")
            location = station_label(origin)
            location += " → " + station_label(destination) if destination else "（駅）"
            number = train.get("odpt:trainNumber") or "番号不明"
            direction = str(train.get("odpt:railDirection") or "方向不明").split(":")[-1]
            kind = str(train.get("odpt:trainType") or "種別不明").split(":")[-1]
            lines.append(f"{number} | {location} | {direction} | {kind}")
        return "\n".join(lines)

    def information(self, name):
        name = name.strip()
        railway_id = None
        operator = operator_for(name)
        if name in INFORMATION_LINES:
            operator, railway_id = INFORMATION_LINES[name]
        elif operator is None:
            parts = name.split(maxsplit=1)
            if len(parts) == 2 and operator_for(parts[0]):
                operator = operator_for(parts[0])
                selected = INFORMATION_LINES.get(parts[1])
                if selected and selected[0] == operator:
                    railway_id = selected[1]
                else:
                    matches = [r for r in self.client.fetch("Railway", operator.id) if parts[1] in {
                        japanese(r.get("odpt:railwayTitle")), japanese(r.get("dc:title")), r.get("owl:sameAs"),
                        str(r.get("owl:sameAs", "")).removeprefix("odpt.Railway:") }]
                    if len(matches) == 1:
                        railway_id = matches[0].get("owl:sameAs")
                    if not railway_id:
                        raise ODPTError("指定した路線が見つかりません。事業者と路線名を確認してください。")
            else:
                raw = name.removeprefix("odpt.Railway:")
                if "." in raw:
                    operator = operator_for(raw.split(".", 1)[0])
                    railway_id = "odpt.Railway:" + raw
        if operator is None:
            raise ODPTError("事業者・路線が見つかりません。「運行情報対応路線」「対応路線」を確認してください。")
        return self.operator_information(name if railway_id else operator.name, operator.id, operator.information_id, railway_id=railway_id)

    def operator_information(self, name, station_operator, information_operator, *, railway_id=None):
        from .legacy.TrainInformation import RailwayInfo
        mapping = RailwayInfo("odpt.Operator:" + information_operator, "").get_railway_mapping()
        try:
            for railway in self.client.fetch("Railway", station_operator):
                mapping[railway.get("owl:sameAs")] = japanese(railway.get("odpt:railwayTitle"), japanese(railway.get("dc:title"), railway.get("owl:sameAs", "不明な路線")))
        except ODPTError:
            pass  # Static titles still work if metadata is temporarily unavailable.
        data = self.client.fetch("TrainInformation", information_operator)
        if railway_id:
            data = [info for info in data if info.get("odpt:railway") == railway_id]
        elif data:
            data = [info for info in data if not is_normal_information(info)]
            if not data:
                return None
        lines = [name + " 運行情報"]
        if not data:
            lines.append("現在、公開されている運行情報はありません。（平常運転の保証ではありません）")
        for info in data:
            railway = info.get("odpt:railway")
            title = mapping.get(railway) or railway or name
            status = japanese(info.get("odpt:trainInformationStatus"))
            content = japanese(info.get("odpt:trainInformationText"), "本文なし")
            lines.append(f"【{title}{' ' + status if status else ''}】{content}")
            updated = info.get("odpt:timeOfOrigin") or info.get("dc:date")
            if updated:
                lines.append(f"更新: {updated}")
        return "\n".join(lines)
