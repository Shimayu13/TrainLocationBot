"""Preserved line-specific implementation; authentication lives in trainbot.config."""
from trainbot.odpt import odpt_get
import requests
import json
import time
import os

def fetch_station_mapping():
    url = '/odpt:Station?odpt:operator=odpt.Operator:Keikyu'
    jsondata = odpt_get(url).json()
    station_mapping = {}
    stations = []
    for station in jsondata:
        railway = station.get('odpt:railway', '')
        station_code = station.get('odpt:stationCode', '')
        if railway == 'odpt.Railway:Keikyu.Main' and station_code.startswith('KK'):
            japanese = station.get('dc:title', '')
            owl_same_as = station.get('owl:sameAs', '')
            romaji = owl_same_as.split('.')[-1]
            if romaji and japanese:
                station_mapping[romaji] = japanese
                stations.append((station_code, japanese))
    stations.sort(reverse=False, key=lambda x: int(x[0][2:]))
    sorted_station_names = [station[1] for station in stations]
    return (station_mapping, sorted_station_names)

def fetch_train_data(station_mapping, sorted_station_names):
    url1 = '/odpt:Train?odpt:operator=odpt.Operator:Keikyu'
    jsondata1 = odpt_get(url1).json()
    station_display = {station: {'outbound': [], 'inbound': [], 'between': {'outbound': [], 'inbound': []}} for station in sorted_station_names}
    for train in jsondata1:
        railway = train.get('odpt:railway', '')
        train_type = train.get('odpt:trainType', '')
        rail_direction = train.get('odpt:railDirection', '')
        from_station_code = train.get('odpt:fromStation', None)
        to_station_code = train.get('odpt:toStation', None)
        if railway == 'odpt.Railway:Keikyu.Main':
            from_station_romaji = from_station_code.split('.')[-1] if from_station_code else None
            to_station_romaji = to_station_code.split('.')[-1] if to_station_code else None
            from_station = station_mapping.get(from_station_romaji, None)
            to_station = station_mapping.get(to_station_romaji, None)
            symbol = ''
            if train_type == 'odpt.TrainType:Keikyu.Local':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = '▽'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = '△'
            elif train_type == 'odpt.TrainType:Keikyu.Express':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = '▼'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = '▲'
            elif train_type == 'odpt.TrainType:Keikyu.LimitedExpress':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = 'v'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = '^'
            elif train_type == 'odpt.TrainType:Keikyu.RapidLimitedExpress':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = '↓'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = '↑'
            elif train_type == 'odpt.TrainType:Keikyu.AirportRapidLimitedExpress':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = 'W'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = 'M'
            elif train_type == 'odpt.TrainType:Keikyu.EveningWing':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = '○'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = '⚫︎'
            elif train_type == 'odpt.TrainType:Keikyu.MorningWing':
                if rail_direction == 'odpt.RailDirection:Outbound':
                    symbol = '○'
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    symbol = '⚫︎'
            if to_station and from_station:
                if rail_direction == 'odpt.RailDirection:Outbound':
                    station_display[from_station]['between']['outbound'].append(symbol)
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    station_display[from_station]['between']['inbound'].append(symbol)
            elif from_station and (not to_station):
                if rail_direction == 'odpt.RailDirection:Outbound':
                    station_display[from_station]['outbound'].append(symbol)
                elif rail_direction == 'odpt.RailDirection:Inbound':
                    station_display[from_station]['inbound'].append(symbol)
    return (sorted_station_names, station_display)

def display_train_positions(stations, station_display):
    os.system('clear')
    for (i, station) in enumerate(stations):
        outbound_symbols = ' '.join(station_display[station]['outbound'])
        inbound_symbols = ' '.join(station_display[station]['inbound'])
        print(f'{outbound_symbols:<5} {station:<15} {inbound_symbols}')
        between_outbound_symbols = ' '.join(station_display[station]['between']['outbound'])
        between_inbound_symbols = ' '.join(station_display[station]['between']['inbound'])
        if between_outbound_symbols or between_inbound_symbols:
            print(f"{between_outbound_symbols:<5} {'':<15} {between_inbound_symbols}")
        else:
            print(f"{'':<5} {'':<15} {'':<15}")
    print(f'△,▽ Local\n▲,▼ Express\nv,^ Limited Express\n↑,↓ Kaitoku Limited Express\nW,M Airport Kaitoku Limited Express\n○,⚫︎ Wing Express')

def format_train_positions(stations, station_display):
    lines = []
    for station in stations:
        outbound_symbols = ' '.join(station_display[station]['outbound'])
        inbound_symbols = ' '.join(station_display[station]['inbound'])
        line = f'{inbound_symbols:<5} {station:<15} {outbound_symbols}'
        lines.append(line)
        between_outbound_symbols = ' '.join(station_display[station]['between']['outbound'])
        between_inbound_symbols = ' '.join(station_display[station]['between']['inbound'])
        if between_outbound_symbols or between_inbound_symbols:
            lines.append(f"{between_inbound_symbols:<5} {'':<15} {between_outbound_symbols}")
        else:
            lines.append(f"{'':<5} {'':<15} {'':<15}")
    lines.append(f'△,▽ Local\n▲,▼ Express\nv,^ Limited Express\n↑,↓ Kaitoku Limited Express\nW,M Airport Kaitoku Limited Express\n○,⚫︎ Wing Express')
    return '\n'.join(lines)

def main():
    (station_mapping, sorted_station_names) = fetch_station_mapping()
    while True:
        (stations, station_display) = fetch_train_data(station_mapping, sorted_station_names)
        display_train_positions(stations, station_display)
        time.sleep(15)
if __name__ == '__main__':
    main()
