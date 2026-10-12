"""Read-only real API rendering checks. Never starts or sends to Discord."""
import argparse
import ast
from dataclasses import replace
import importlib
from pathlib import Path
import sys

from trainbot.catalog import LEGACY_LINES, TOBU_LINES
from trainbot.config import Settings
from trainbot.odpt import ODPTClient, ODPTError
from trainbot.services import RailwayService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-standard-key", action="store_true", help="Test JRE's challenge key against standard API without saving it")
    parser.add_argument("--details", action="store_true", help="Show public SotetsuDirect station-order metadata")
    args = parser.parse_args()
    settings = Settings.load()
    if args.details:
        client = ODPTClient(settings)
        rail = "odpt.Railway:JR-East.SotetsuDirect"
        for railway in client.fetch("Railway", "JR-East"):
            if railway.get("owl:sameAs") == rail:
                print(railway.get("odpt:stationOrder"))
        for station in client.fetch("Station", "JR-East"):
            if station.get("odpt:railway") == rail:
                print(station.get("owl:sameAs"), station.get("dc:title"), station.get("odpt:stationCode"))
        print("Published positions:", [(t.get("odpt:fromStation"), t.get("odpt:toStation")) for t in client.fetch("Train", "JR-East") if t.get("odpt:railway") == rail])
        return
    if args.test_standard_key:
        client = ODPTClient(replace(settings, standard_key=settings.challenge_key))
        try:
            rows = client.fetch("Train", "YokohamaMunicipal")
            print("JRE key on standard API: SUCCESS", len(rows), "trains")
        except ODPTError as error:
            print("JRE key on standard API:", str(error))
            return
    service = RailwayService()
    failures = 0
    for name in {**LEGACY_LINES, **TOBU_LINES}:
        try:
            result = service.positions(name)
            print(name, "OK", len(result), "characters")
            if name in LEGACY_LINES:
                module = importlib.import_module("trainbot.legacy.TrainLocation" + LEGACY_LINES[name])
                mapping, names = module.fetch_station_mapping(module.fetch_station_index_mapping()) if hasattr(module, "fetch_station_index_mapping") else module.fetch_station_mapping()
                _, display = module.fetch_train_data(mapping, names)
                def markers(value):
                    if isinstance(value, dict): return sum(markers(v) for v in value.values())
                    if isinstance(value, list): return sum(bool(v) for v in value)
                    return 0
                tree = ast.parse(Path(module.__file__).read_text())
                railways = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("odpt.Railway:")}
                if railways:
                    operator = next(iter(railways)).split(":", 1)[1].split(".", 1)[0]
                    published = sum(t.get("odpt:railway") in railways for t in service.client.fetch("Train", operator))
                    if published and not markers(display):
                        failures += 1
                        print(name, "FAIL: published trains but no visible markers")
                        print("Published types/directions:", sorted({(str(t.get('odpt:trainType')), str(t.get('odpt:railDirection'))) for t in service.client.fetch('Train', operator) if t.get('odpt:railway') in railways}))
        except Exception as error:
            failures += 1
            print(name, "FAIL", str(error) if isinstance(error, ODPTError) else type(error).__name__)
    sys.exit(bool(failures))


if __name__ == "__main__":
    main()
