"""Preserved line-specific implementation; authentication lives in trainbot.config."""
from trainbot.odpt import odpt_get
import requests
import json
import time
import os

def fetch_station_mapping():
    url = '/odpt:Station?odpt:operator=odpt.Operator:JR-East'
    jsondata = odpt_get(url).json()
    station_mapping = {}
    stations = []
    for station in jsondata:
        railway = station.get('odpt:railway', '')
        station_code = station.get('odpt:stationCode', '')
        if railway == 'odpt.Railway:JR-East.Yamanote' and station_code.startswith('JY'):
            japanese = station.get('dc:title', '')
            owl_same_as = station.get('owl:sameAs', '')
            romaji = owl_same_as.split('.')[-1]
            if romaji and japanese:
                station_mapping[romaji] = japanese
                stations.append((station_code, japanese))
    stations.sort(reverse=True, key=lambda x: int(x[0][2:]))
    sorted_station_names = [station[1] for station in stations]
    return (station_mapping, sorted_station_names)

def fetch_train_data(station_mapping, sorted_station_names):
    url1 = '/odpt:Train?odpt:operator=odpt.Operator:JR-East'
    jsondata1 = odpt_get(url1).json()
    station_display = {station: {'outerloop': [], 'innerloop': [], 'between': {'outerloop': [], 'innerloop': []}} for station in sorted_station_names}
    for train in jsondata1:
        railway = train.get('odpt:railway', '')
        train_type = train.get('odpt:trainType', '')
        rail_direction = train.get('odpt:railDirection', '')
        from_station_code = train.get('odpt:fromStation', None)
        to_station_code = train.get('odpt:toStation', None)
        if railway == 'odpt.Railway:JR-East.Yamanote':
            from_station_romaji = from_station_code.split('.')[-1] if from_station_code else None
            to_station_romaji = to_station_code.split('.')[-1] if to_station_code else None
            from_station = station_mapping.get(from_station_romaji, None)
            to_station = station_mapping.get(to_station_romaji, None)
            symbol = ''
            if rail_direction == 'odpt.RailDirection:OuterLoop':
                symbol = '▽'
            elif rail_direction == 'odpt.RailDirection:InnerLoop':
                symbol = '△'
            if to_station and from_station:
                if rail_direction == 'odpt.RailDirection:OuterLoop':
                    station_display[from_station]['between']['outerloop'].append(symbol)
                elif rail_direction == 'odpt.RailDirection:InnerLoop':
                    station_display[from_station]['between']['innerloop'].append(symbol)
            elif from_station and (not to_station):
                if rail_direction == 'odpt.RailDirection:OuterLoop':
                    station_display[from_station]['outerloop'].append(symbol)
                elif rail_direction == 'odpt.RailDirection:InnerLoop':
                    station_display[from_station]['innerloop'].append(symbol)
    return (sorted_station_names, station_display)

def display_train_positions(stations, station_display):
    os.system('clear')
    for (i, station) in enumerate(stations):
        outerloop_symbols = ' '.join(station_display[station]['outerloop'])
        innerloop_symbols = ' '.join(station_display[station]['innerloop'])
        print(f'{outerloop_symbols:<5} {station:<15} {innerloop_symbols}')
        between_outerloop_symbols = ' '.join(station_display[station]['between']['outerloop'])
        between_innerloop_symbols = ' '.join(station_display[station]['between']['innerloop'])
        if between_outerloop_symbols or between_innerloop_symbols:
            print(f"{between_innerloop_symbols:<5} {'':<15} {between_outerloop_symbols}")
        else:
            print(f"{'':<5} {'':<15} {'':<15}")
        if station == '東京':
            print(f"{innerloop_symbols:<5} {'有楽町':<15} {outerloop_symbols}")

def format_train_positions(stations, station_display):
    lines = []
    for (i, station) in enumerate(stations):
        outerloop_symbols = ' '.join(station_display[station]['outerloop'])
        innerloop_symbols = ' '.join(station_display[station]['innerloop'])
        line = f'{innerloop_symbols:<5} {station:<15} {outerloop_symbols}'
        lines.append(line)
        between_outerloop_symbols = ' '.join(station_display[station]['between']['outerloop'])
        between_innerloop_symbols = ' '.join(station_display[station]['between']['innerloop'])
        if between_outerloop_symbols or between_innerloop_symbols:
            lines.append(f"{between_innerloop_symbols:<5} {'':<15} {between_outerloop_symbols}")
        else:
            lines.append(f"{'':<5} {'':<15} {'':<15}")
        if station == '東京':
            outerloop_symbols_t = ' '.join(station_display['有楽町']['outerloop'])
            innerloop_symbols_t = ' '.join(station_display['有楽町']['innerloop'])
            lines.append(f"{innerloop_symbols_t:<5} {'有楽町':<15} {outerloop_symbols_t}")
    return '\n'.join(lines)

def main():
    (station_mapping, sorted_station_names) = fetch_station_mapping()
    while True:
        (stations, station_display) = fetch_train_data(station_mapping, sorted_station_names)
        display_train_positions(stations, station_display)
        time.sleep(15)
if __name__ == '__main__':
    main()
