from dataclasses import dataclass


@dataclass(frozen=True)
class Operator:
    name: str
    id: str
    information_id: str
    endpoint: str


OPERATORS = {
    "JR": Operator("JR東日本", "JR-East", "jre-is", "challenge"),
    "都営": Operator("都営地下鉄", "Toei", "Toei", "public"),
    "東武": Operator("東武鉄道", "Tobu", "Tobu", "challenge"),
    "横浜市営": Operator("横浜市営地下鉄", "YokohamaMunicipal", "YokohamaMunicipal", "standard"),
    "京急": Operator("京急電鉄", "Keikyu", "Keikyu", "challenge"),
    "西武": Operator("西武電鉄", "Seibu", "Seibu", "challenge"),
    "東急": Operator("東急電鉄", "Tokyu", "Tokyu", "challenge"),
    "東京メトロ": Operator("東京メトロ", "TokyoMetro", "TokyoMetro", "standard"),
    "つくばエクスプレス": Operator("つくばエクスプレス", "MIR", "MIR", "standard"),
    "多摩都市モノレール": Operator("多摩都市モノレール", "TamaMonorail", "TamaMonorail", "standard"),
    "りんかい線": Operator("東京臨海高速鉄道", "TWR", "TWR", "standard"),
}
ALIASES = {"JR東日本": "JR", "都営地下": "都営", "都営地下鉄": "都営", "東武鉄道": "東武", "横浜市営地下鉄": "横浜市営", "京急電鉄": "京急"}


def operator_for(name):
    name = name.strip()
    operator = OPERATORS.get(ALIASES.get(name, name))
    return operator or next((item for item in OPERATORS.values() if name in {item.name, item.id, item.information_id}), None)


# Each module keeps its original station order and train symbols.
LEGACY_LINES = {
    "ブルーライン": "BL", "グリーンライン": "GL",
    "都営浅草線": "TokyoAsakusa", "都営三田線": "TokyoMita",
    "都営大江戸線": "TokyoOedo", "都営新宿線": "TokyoShinjuku",
    "京急本線": "KK_main", "京急空港線": "KK_airport", "京急大師線": "KK_daishi",
    "京急逗子線": "KK_zushi", "京急久里浜線": "KK_kuri",
    "山手線": "JY", "中央線快速": "JC", "中央本線": "CO", "中央線快速別表示": "JCG",
    "中央総武線各駅停車": "JB", "青梅線": "JC_O", "五日市線": "JC_I",
    "埼京川越線": "JA", "相鉄直通線": "JA_SO", "川越線": "JREKA",
    "湘南新宿ライン": "JS", "京葉線": "JE", "横浜線": "JH",
    "常磐線快速": "JJ", "常磐線各駅停車": "JL", "京浜東北根岸線": "JK",
    "武蔵野線": "JM", "南武線": "JN", "横須賀線": "JO", "総武線快速": "JOS",
    "東海道線": "JT", "宇都宮線": "JU", "高崎線": "JU_Taka", "東武東上線": "TJ",
    "都営浅草線別表示": "TokyoA",
}
for name, module in list(LEGACY_LINES.items()):
    if module.startswith("KK_"):
        LEGACY_LINES[name + "列車番号"] = module + "_number"

TOBU_LINES = {
    "東武スカイツリーライン": "TobuSkytree",
    "東武スカイツリーライン支線": "TobuSkytreeBranch",
    "東武伊勢崎線": "Isesaki", "東武日光線": "Nikko",
    "東武アーバンパークライン": "TobuUrbanPark", "東武越生線": "Ogose",
    "東武亀戸線": "Kameido", "東武大師線": "Daishi",
}
COVERAGE = "東武の列車位置は館林以北・新栃木以北が対象外。亀戸線の小村井〜東あずま〜亀戸水神は在線位置を区別できません。"
