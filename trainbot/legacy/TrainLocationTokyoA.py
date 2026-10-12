"""Preserved line-specific implementation; authentication lives in trainbot.config."""
from trainbot.odpt import odpt_get
import requests
import json
import time
import os

def fetch_station_mapping():
    url = '/odpt:Station?odpt:operator=odpt.Operator:Toei'
    jsondata = odpt_get(url).json()
    station_mapping = {}
    stations = []
    for station in jsondata:
        railway = station.get('odpt:railway', '')
        station_code = station.get('odpt:stationCode', '')
        if railway == 'odpt.Railway:Toei.Asakusa' and station_code.startswith('A-'):
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
    url1 = '/odpt:Train?odpt:operator=odpt.Operator:Toei'
    jsondata1 = odpt_get(url1).json()
    station_display = {station: {'southbound': [], 'northbound': [], 'between': {'southbound': [], 'northbound': []}} for station in sorted_station_names}
    for train in jsondata1:
        railway = train.get('odpt:railway', '')
        train_type = train.get('odpt:trainType', '')
        rail_direction = train.get('odpt:railDirection', '')
        from_station_code = train.get('odpt:fromStation', None)
        to_station_code = train.get('odpt:toStation', None)
        train_number = train.get('odpt:trainNumber', '')
        if railway == 'odpt.Railway:Toei.Asakusa':
            from_station_romaji = from_station_code.split('.')[-1] if from_station_code else None
            to_station_romaji = to_station_code.split('.')[-1] if to_station_code else None
            from_station = station_mapping.get(from_station_romaji, None)
            to_station = station_mapping.get(to_station_romaji, None)
            symbol = ''
            if rail_direction == 'odpt.RailDirection:Southbound':
                if train_type == 'odpt.TrainType:Toei.Local':
                    symbol = '▽'
                elif train_type == 'odpt.TrainType:Toei.Express':
                    symbol = '▽'
                elif train_type == 'odpt.TrainType:Toei.Rapid':
                    symbol = '▽'
                elif train_type == 'odpt.TrainType:Toei.LimitedExpress':
                    symbol = '▽'
                elif train_type == 'odpt.TrainType:Toei.AirportRapidLimitedExpress':
                    symbol = '▼'
                else:
                    symbol = '▽'
            elif rail_direction == 'odpt.RailDirection:Northbound':
                if train_type == 'odpt.TrainType:Toei.Local':
                    symbol = '△'
                elif train_type == 'odpt.TrainType:Toei.Express':
                    symbol = '△'
                elif train_type == 'odpt.TrainType:Toei.Rapid':
                    symbol = '△'
                elif train_type == 'odpt.TrainType:Toei.LimitedExpress':
                    symbol = '△'
                elif train_type == 'odpt.TrainType:Toei.AirportRapidLimitedExpress':
                    symbol = '▲'
                else:
                    symbol = '△'
            display_symbol = f'{symbol}{train_number}'
            if to_station and from_station:
                if rail_direction == 'odpt.RailDirection:Southbound':
                    station_display[from_station]['between']['southbound'].append(display_symbol)
                elif rail_direction == 'odpt.RailDirection:Northbound':
                    station_display[from_station]['between']['northbound'].append(display_symbol)
            elif from_station and (not to_station):
                if rail_direction == 'odpt.RailDirection:Southbound':
                    station_display[from_station]['southbound'].append(display_symbol)
                elif rail_direction == 'odpt.RailDirection:Northbound':
                    station_display[from_station]['northbound'].append(display_symbol)
    return (sorted_station_names, station_display)

def display_train_positions(stations, station_display):
    os.system('clear')
    for (i, station) in enumerate(stations):
        southbound_symbols = ' '.join(station_display[station]['southbound'])
        northbound_symbols = ' '.join(station_display[station]['northbound'])
        print(f'{southbound_symbols:<20} {station:<15} {northbound_symbols}')
        between_southbound_symbols = ' '.join(station_display[station]['between']['southbound'])
        between_northbound_symbols = ' '.join(station_display[station]['between']['northbound'])
        if between_southbound_symbols or between_northbound_symbols:
            print(f"{between_southbound_symbols:<20} {'':<15} {between_northbound_symbols}")
        else:
            print(f"{'':<20} {'':<15} {'':<15}")

def format_train_positions(stations, station_display):
    lines = []
    for (i, station) in enumerate(stations):
        southbound_symbols = ' '.join(station_display[station]['southbound'])
        northbound_symbols = ' '.join(station_display[station]['northbound'])
        line = f'{southbound_symbols:<20} {station:<15} {northbound_symbols}'
        lines.append(line)
        between_southbound_symbols = ' '.join(station_display[station]['between']['southbound'])
        between_northbound_symbols = ' '.join(station_display[station]['between']['northbound'])
        if between_southbound_symbols or between_northbound_symbols:
            lines.append(f"{between_southbound_symbols:<20} {'':<15} {between_northbound_symbols}")
        else:
            lines.append(f"{'':<20} {'':<15} {'':<15}")
    return '\n'.join(lines)

def main():
    (station_mapping, sorted_station_names) = fetch_station_mapping()
    while True:
        (stations, station_display) = fetch_train_data(station_mapping, sorted_station_names)
        display_train_positions(stations, station_display)
        time.sleep(15)
if __name__ == '__main__':
    main()
