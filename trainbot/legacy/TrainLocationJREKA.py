"""Preserved line-specific implementation; authentication lives in trainbot.config."""
from trainbot.odpt import odpt_get
import requests
import time
import os

def fetch_station_index_mapping():
    url = '/odpt:Railway?odpt:operator=odpt.Operator:JR-East'
    jsondata = odpt_get(url).json()
    station_index_mapping = {}
    for railway in jsondata:
        if railway.get('owl:sameAs') == 'odpt.Railway:JR-East.Kawagoe':
            station_order = railway.get('odpt:stationOrder', [])
            for station_info in station_order:
                station_code = station_info['odpt:station'].split('.')[-1]
                index = station_info['odpt:index']
                station_index_mapping[station_code] = index
    return station_index_mapping

def fetch_station_mapping(station_index_mapping):
    url = '/odpt:Station?odpt:operator=odpt.Operator:JR-East'
    jsondata = odpt_get(url).json()
    station_mapping = {}
    stations = []
    for station in jsondata:
        railway = station.get('odpt:railway', '')
        station_code = station.get('owl:sameAs', '').split('.')[-1]
        if railway == 'odpt.Railway:JR-East.Kawagoe':
            japanese = station.get('dc:title', '')
            romaji = station_code
            if romaji and japanese:
                station_mapping[romaji] = japanese
                index = station_index_mapping.get(romaji, float('inf'))
                stations.append((index, japanese))
    stations.sort(key=lambda x: x[0])
    sorted_station_names = [station[1] for station in stations]
    return (station_mapping, sorted_station_names)

def fetch_train_data(station_mapping, sorted_station_names):
    url = '/odpt:Train?odpt:operator=odpt.Operator:JR-East'
    jsondata = odpt_get(url).json()
    station_display = {station: {'outbound': [], 'inbound': [], 'between': {'outbound': [], 'inbound': []}} for station in sorted_station_names}
    for train in jsondata:
        railway = train.get('odpt:railway', '')
        train_type = train.get('odpt:trainType', '')
        rail_direction = train.get('odpt:railDirection', '')
        from_station_code = train.get('odpt:fromStation', None)
        to_station_code = train.get('odpt:toStation', None)
        if railway == 'odpt.Railway:JR-East.Kawagoe':
            from_station_romaji = from_station_code.split('.')[-1] if from_station_code else None
            to_station_romaji = to_station_code.split('.')[-1] if to_station_code else None
            from_station = station_mapping.get(from_station_romaji, None)
            to_station = station_mapping.get(to_station_romaji, None)
            symbol = ''
            if train_type == 'odpt.TrainType:JR-East.Local':
                symbol = '▽' if rail_direction == 'odpt.RailDirection:Outbound' else '△'
            elif train_type == 'odpt.TrainType:JR-East.Rapid':
                symbol = '▼' if rail_direction == 'odpt.RailDirection:Outbound' else '▲'
            elif train_type == 'odpt.TrainType:JR-East.LimitedExpress':
                symbol = '↓' if rail_direction == 'odpt.RailDirection:Outbound' else '↑'
            if from_station and to_station:
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
    for station in stations:
        outbound_symbols = ' '.join(station_display[station]['outbound'])
        inbound_symbols = ' '.join(station_display[station]['inbound'])
        print(f'{inbound_symbols:<5} {station:<15} {outbound_symbols}')
        between_outbound_symbols = ' '.join(station_display[station]['between']['outbound'])
        between_inbound_symbols = ' '.join(station_display[station]['between']['inbound'])
        if between_outbound_symbols or between_inbound_symbols:
            print(f"{between_inbound_symbols:<5} {'':<15} {between_outbound_symbols}")
        else:
            print(f"{'':<5} {'':<15} {'':<15}")

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
    return '\n'.join(lines)

def main():
    station_index_mapping = fetch_station_index_mapping()
    (station_mapping, sorted_station_names) = fetch_station_mapping(station_index_mapping)
    while True:
        (stations, station_display) = fetch_train_data(station_mapping, sorted_station_names)
        display_train_positions(stations, station_display)
        time.sleep(15)
if __name__ == '__main__':
    main()
