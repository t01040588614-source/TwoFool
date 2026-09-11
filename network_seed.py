"""전국 SCMAGLEV 네트워크 시드 데이터 (역 · 노선 · 열차).

행정구역 기준 대한민국 전체 시(市)만 포함하며, 군(郡)은 제외합니다.
"""

from __future__ import annotations

import math
from typing import Iterable

# (code, name, latitude, longitude)
STATION_SPECS: list[tuple[str, str, float, float]] = [
    # 특별·광역·특별자치시
    ("SEO", "서울", 37.5665, 126.9780),
    ("YON", "용산", 37.5299, 126.9648),
    ("ICN", "인천", 37.4563, 126.7052),
    ("SEJ", "세종", 36.4800, 127.2890),
    ("DAE", "대전", 36.3504, 127.3845),
    ("GWJ", "광주", 35.1595, 126.8526),
    ("DGU", "대구", 35.8714, 128.6014),
    ("ULS", "울산", 35.5384, 129.3114),
    ("BUS", "부산", 35.1796, 129.0756),
    # 경기도 (31개 시)
    ("SUW", "수원", 37.2636, 127.0286),
    ("SNG", "성남", 37.4200, 127.1265),
    ("UJB", "의정부", 37.7381, 127.0338),
    ("ANY", "안양", 37.3943, 126.9568),
    ("BUC", "부천", 37.5034, 126.7660),
    ("GIM", "광명", 37.4162, 126.8843),
    ("PTG", "평택", 36.9921, 127.1129),
    ("DGC", "동두천", 37.9034, 127.0606),
    ("ASR", "안산", 37.3219, 126.8309),
    ("GOY", "고양", 37.6584, 126.8320),
    ("GWC", "과천", 37.4292, 126.9877),
    ("GRI", "구리", 37.5944, 127.1296),
    ("NYJ", "남양주", 37.6360, 127.2165),
    ("OSN", "오산", 37.1498, 127.0775),
    ("SIH", "시흥", 37.3800, 126.8030),
    ("GNP", "군포", 37.3615, 126.9353),
    ("UIW", "의왕", 37.3449, 126.9484),
    ("HAN", "하남", 37.5394, 127.2149),
    ("YNI", "용인", 37.2411, 127.1776),
    ("PAJ", "파주", 37.7597, 126.7800),
    ("ICH", "이천", 37.2720, 127.4350),
    ("ANS", "안성", 37.0075, 127.2797),
    ("GMP", "김포", 37.6153, 126.7155),
    ("HWS", "화성", 37.1999, 126.8315),
    ("GGI", "경기광주", 37.4295, 127.2550),
    ("YNG", "양주", 37.7851, 127.0457),
    ("POC", "포천", 38.0319, 127.2342),
    ("YJU", "여주", 37.2983, 127.6371),
    # 강원특별자치도 (7개 시)
    ("CHC", "춘천", 37.8813, 127.7298),
    ("WON", "원주", 37.3422, 127.9202),
    ("GAN", "강릉", 37.7519, 128.8761),
    ("DHE", "동해", 37.5249, 129.1146),
    ("TAE", "태백", 37.1644, 128.9856),
    ("SOK", "속초", 38.2070, 128.5918),
    ("SAM", "삼척", 37.4499, 129.1653),
    # 충청북도 (3개 시)
    ("CHJ", "청주", 36.6424, 127.4890),
    ("CHG", "충주", 36.9910, 127.9260),
    ("JCH", "제천", 37.1326, 128.1910),
    # 충청남도 (8개 시)
    ("CHE", "천안", 36.8151, 127.1139),
    ("GOJ", "공주", 36.4465, 127.1190),
    ("BRY", "보령", 36.3332, 126.6128),
    ("ASN", "아산", 36.7898, 127.0019),
    ("SES", "서산", 36.7848, 126.4503),
    ("NON", "논산", 36.1867, 127.0983),
    ("GYR", "계룡", 36.2749, 127.2486),
    ("DNJ", "당진", 36.8947, 126.6297),
    # 전라북도 (6개 시)
    ("JEO", "전주", 35.8242, 127.1480),
    ("GUN", "군산", 35.9677, 126.7366),
    ("IKS", "익산", 35.9483, 126.9575),
    ("JEU", "정읍", 35.5696, 126.8560),
    ("NAM", "남원", 35.4164, 127.3904),
    ("GJE", "김제", 35.8036, 126.8808),
    # 전라남도 (5개 시)
    ("MOK", "목포", 34.8118, 126.3922),
    ("YSU", "여수", 34.7604, 127.6622),
    ("SUN", "순천", 34.9507, 127.4872),
    ("NAJ", "나주", 35.0156, 126.7108),
    ("GYJ", "광양", 34.9404, 127.6956),
    # 경상북도 (10개 시)
    ("POH", "포항", 36.0190, 129.3435),
    ("GYE", "경주", 35.8562, 129.2247),
    ("GIC", "김천", 36.1396, 128.1136),
    ("AND", "안동", 36.5684, 128.7294),
    ("GUM", "구미", 36.1195, 128.3445),
    ("YOJ", "영주", 36.8057, 128.6241),
    ("YCH", "영천", 35.9733, 128.9387),
    ("SJU", "상주", 36.4108, 128.1590),
    ("MGG", "문경", 36.5948, 128.1874),
    ("GYS", "경산", 35.8250, 128.7373),
    # 경상남도 (8개 시, 울산은 광역시로 상단 포함)
    ("CHW", "창원", 35.2280, 128.6811),
    ("JIN", "진주", 35.1800, 128.1076),
    ("TYG", "통영", 34.8544, 128.4333),
    ("SCJ", "사천", 35.0038, 128.0640),
    ("GHM", "김해", 35.2285, 128.8890),
    ("MLY", "밀양", 35.5036, 128.7464),
    ("GEJ", "거제", 34.8806, 128.6211),
    ("YSN", "양산", 35.3359, 129.0377),
    # 제주특별자치도 (2개 시)
    ("CJU", "제주", 33.4996, 126.5312),
    ("SGP", "서귀포", 33.2541, 126.5601),
]

# (route_code, route_name, station_codes)
ROUTE_SPECS: list[tuple[str, str, list[str]]] = [
    ("R-SEOUL-01", "서울권 순환", ["SEO", "YON", "GIM", "SUW", "CHE", "ASN"]),
    ("R-METRO-INCHEON", "수도권 서부", ["ICN", "GMP", "GOY", "PAJ", "GIM", "ASR", "ANY", "SUW"]),
    ("R-GYEONGGI-NORTH", "경기 북부", ["SEO", "GRI", "NYJ", "UJB", "YNG", "DGC", "POC"]),
    ("R-GYEONGGI-EAST", "경기 동부", ["SEO", "HAN", "SNG", "ICH", "YJU", "WON"]),
    ("R-GYEONGGI-SOUTH", "경기 남부", ["SUW", "OSN", "PTG", "HWS", "ANS", "CHE"]),
    ("R-GYEONGGI-WEST", "경기 서부", ["ICN", "BUC", "SIH", "GNP", "UIW", "YNI", "SUW"]),
    ("R-GYEONGGI-CENTER", "경기 중부", ["GOY", "GWC", "ANY", "GNP", "SNG", "YNI", "GGI"]),
    ("R-EAST-AXIS", "수도권-강원권", ["SEO", "YON", "CHC", "WON", "DHE", "GAN"]),
    ("R-GANGWON-INLAND", "강원 내륙", ["CHC", "WON", "TAE", "GAN"]),
    ("R-GANGWON-COAST", "강원 동해", ["SOK", "GAN", "DHE", "SAM", "POH"]),
    ("R-CHUNGCHEONG", "충청권", ["SEJ", "DAE", "CHJ", "CHG", "JCH", "WON", "CHE"]),
    ("R-CHUNGCHEONG-WEST", "충남 서부", ["CHE", "ASN", "GOJ", "BRY", "DNJ", "SES", "GUN"]),
    ("R-SEOUL-BUSAN", "경부 고속", ["SEO", "SEJ", "CHE", "DAE", "GUM", "GIC", "DGU", "GYS", "GYE", "ULS", "BUS"]),
    ("R-HONAM", "호남 고속", ["SEO", "DAE", "NON", "JEO", "GWJ", "NAJ", "MOK"]),
    ("R-HONAM-EAST", "호남 동부", ["DAE", "NON", "JEO", "SUN", "GYJ", "YSU"]),
    ("R-JEOLLA-CROSS", "전라 횡단", ["GUN", "IKS", "GJE", "JEO", "NAM", "JEU", "GWJ", "SUN", "YSU"]),
    ("R-SOUTH-CROSS", "중부-남해", ["DAE", "JEO", "JIN", "SCJ", "BUS"]),
    ("R-DONGHAE", "동해축", ["BUS", "YSN", "ULS", "POH", "SAM", "DHE", "GAN", "TYG", "GEJ"]),
    ("R-METRO-YEONGNAM", "영남권 광역", ["DGU", "GIC", "YOJ", "AND", "POH", "GYE", "ULS", "BUS", "CHW", "GHM"]),
    ("R-GYEONGSANG-SOUTH", "경남 남부", ["CHW", "GHM", "GEJ", "TYG", "JIN", "SCJ", "MLY", "BUS"]),
    ("R-GYEONGSANG-WEST", "경북-경남", ["GUM", "GIC", "DGU", "MGG", "SJU", "YCH", "JIN", "CHW"]),
    ("R-JEJU-LINK", "제주 연결", ["MOK", "YSU", "CJU", "SGP"]),
    ("R-SEOUL-JEJU", "서울-제주 직결", ["SEO", "SEJ", "DAE", "NON", "JEO", "MOK", "YSU", "CJU"]),
    ("R-DIRECT-NATIONAL", "전국 직통", ["SEO", "BUS", "CJU"]),
]

# (train_number, train_name, type_code, route_code, cars, dep, arr, duration_minutes, offset_minutes)
FLAGSHIP_TRAIN_SPECS: list[tuple] = [
    ("SM-1001", "서울권 1001", "METRO", "R-SEOUL-01", 8, "SEO", "SUW", 38, -35),
    ("SM-1002", "서울권 1002", "METRO", "R-SEOUL-01", 8, "YON", "CHE", 42, -28),
    ("SM-1003", "서울권 1003", "METRO", "R-SEOUL-01", 8, "GIM", "ASN", 45, -20),
    ("SM-1004", "서울권 1004", "METRO", "R-SEOUL-01", 8, "SUW", "SEO", 40, -10),
    ("SM-1005", "수도권 1005", "METRO", "R-METRO-INCHEON", 8, "ICN", "SEO", 36, -24),
    ("SM-1006", "수도권 1006", "METRO", "R-METRO-INCHEON", 8, "SEO", "PAJ", 34, -12),
    ("SM-1101", "영남권 1101", "METRO", "R-METRO-YEONGNAM", 8, "DGU", "BUS", 78, -18),
    ("SM-1102", "영남권 1102", "METRO", "R-METRO-YEONGNAM", 8, "BUS", "POH", 62, -6),
    ("SM-1201", "강원권 1201", "METRO", "R-EAST-AXIS", 8, "YON", "CHC", 48, -14),
    ("SM-1202", "강원권 1202", "METRO", "R-EAST-AXIS", 8, "CHC", "GAN", 58, -4),
    ("SM-5001", "서울부산 5001", "HIGHSPEED", "R-SEOUL-BUSAN", 10, "SEO", "BUS", 165, -30),
    ("SM-5002", "서울부산 5002", "HIGHSPEED", "R-SEOUL-BUSAN", 10, "DAE", "BUS", 112, -16),
    ("SM-5003", "서울부산 5003", "HIGHSPEED", "R-SEOUL-BUSAN", 10, "SEO", "DGU", 130, -8),
    ("SM-5004", "서울부산 5004", "HIGHSPEED", "R-SEOUL-BUSAN", 10, "DGU", "SEO", 128, 5),
    ("SM-5005", "동해축 5005", "HIGHSPEED", "R-EAST-AXIS", 10, "SEO", "GAN", 108, -22),
    ("SM-5006", "호남축 5006", "HIGHSPEED", "R-HONAM", 10, "SEO", "MOK", 152, -18),
    ("SM-5007", "호남축 5007", "HIGHSPEED", "R-HONAM", 10, "GWJ", "SEO", 138, -3),
    ("SM-5008", "남해축 5008", "HIGHSPEED", "R-SOUTH-CROSS", 10, "DAE", "BUS", 126, -9),
    ("SM-5009", "동남권 5009", "HIGHSPEED", "R-DONGHAE", 10, "BUS", "GAN", 140, -1),
    ("SM-5010", "전국순환 5010", "HIGHSPEED", "R-SEOUL-BUSAN", 10, "BUS", "SEO", 160, 7),
    ("SM-5011", "포천급행 5011", "HIGHSPEED", "R-GYEONGGI-NORTH", 10, "SEO", "POC", 52, 12),
    ("SM-5012", "포천급행 5012", "HIGHSPEED", "R-GYEONGGI-NORTH", 10, "POC", "SEO", 54, 28),
    ("SM-5013", "이천급행 5013", "HIGHSPEED", "R-GYEONGGI-EAST", 10, "SEO", "ICH", 48, 18),
    ("SM-5014", "이천급행 5014", "HIGHSPEED", "R-GYEONGGI-EAST", 10, "ICH", "SEO", 46, 34),
    ("SM-5015", "원주급행 5015", "HIGHSPEED", "R-GYEONGGI-EAST", 10, "SEO", "WON", 72, 22),
    ("SM-5016", "원주급행 5016", "HIGHSPEED", "R-GANGWON-INLAND", 10, "WON", "GAN", 58, 38),
    ("SM-5017", "여수급행 5017", "HIGHSPEED", "R-HONAM-EAST", 10, "SEO", "YSU", 168, 8),
    ("SM-5018", "여수급행 5018", "HIGHSPEED", "R-HONAM-EAST", 10, "YSU", "SEO", 170, 24),
    ("SM-5019", "거제급행 5019", "HIGHSPEED", "R-GYEONGSANG-SOUTH", 10, "BUS", "GEJ", 42, 14),
    ("SM-5020", "거제급행 5020", "HIGHSPEED", "R-GYEONGSANG-SOUTH", 10, "GEJ", "SEO", 175, 30),
    ("SM-5021", "제주급행 5021", "HIGHSPEED", "R-JEJU-LINK", 10, "MOK", "CJU", 95, 44),
    ("SM-5022", "제주급행 5022", "HIGHSPEED", "R-JEJU-LINK", 10, "CJU", "YSU", 88, 58),
    ("SM-5023", "세종급행 5023", "HIGHSPEED", "R-CHUNGCHEONG", 10, "SEO", "SEJ", 68, 16),
    ("SM-5024", "부천급행 5024", "HIGHSPEED", "R-GYEONGGI-WEST", 10, "SEO", "BUC", 38, 20),
    ("SM-5025", "용인급행 5025", "HIGHSPEED", "R-GYEONGGI-WEST", 10, "SEO", "YNI", 44, 26),
    ("SM-5026", "서귀포 5026", "HIGHSPEED", "R-JEJU-LINK", 10, "CJU", "SGP", 28, 48),
    ("SM-5027", "양산급행 5027", "HIGHSPEED", "R-DONGHAE", 10, "BUS", "YSN", 22, 32),
    ("SM-5028", "광양급행 5028", "HIGHSPEED", "R-HONAM-EAST", 10, "SUN", "GYJ", 18, 36),
    ("SM-5030", "서울제주 5030", "HIGHSPEED", "R-SEOUL-JEJU", 10, "SEO", "CJU", 235, 14),
    ("SM-5031", "제주서울 5031", "HIGHSPEED", "R-SEOUL-JEJU", 10, "CJU", "SEO", 238, 30),
    ("SM-5032", "서울제주 5032", "HIGHSPEED", "R-SEOUL-JEJU", 10, "SEO", "CJU", 235, 54),
    ("SM-5033", "제주서울 5033", "HIGHSPEED", "R-SEOUL-JEJU", 10, "CJU", "SEO", 238, 70),
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _estimate_duration_minutes(
    station_coords: dict[str, tuple[float, float]],
    dep_code: str,
    arr_code: str,
    train_type: str,
) -> int:
    dep = station_coords.get(dep_code)
    arr = station_coords.get(arr_code)
    if not dep or not arr:
        return 35
    km = _haversine_km(dep[0], dep[1], arr[0], arr[1])
    avg_speed = 380 if train_type == "HIGHSPEED" else 185
    minutes = int((km / max(avg_speed, 1)) * 60) + 10
    return max(18, min(minutes, 240))


def _segment_train_specs(
    station_coords: dict[str, tuple[float, float]],
    route_code: str,
    route_name: str,
    station_codes: list[str],
    train_type: str = "METRO",
    cars: int = 8,
    number_prefix: str = "SM-3",
    offset_base: int = 100,
) -> list[tuple]:
    specs: list[tuple] = []
    for index in range(len(station_codes) - 1):
        dep_code = station_codes[index]
        arr_code = station_codes[index + 1]
        duration = _estimate_duration_minutes(station_coords, dep_code, arr_code, train_type)
        train_number = f"{number_prefix}{index + 1:02d}-{dep_code}-{arr_code}"
        train_name = f"{route_name} {dep_code}→{arr_code}"
        offset = offset_base + index * 4
        specs.append((train_number, train_name, train_type, route_code, cars, dep_code, arr_code, duration, offset))
        specs.append((
            f"{train_number}-R",
            f"{route_name} {arr_code}→{dep_code}",
            train_type,
            route_code,
            cars,
            arr_code,
            dep_code,
            duration,
            offset + 2,
        ))
    return specs


def build_train_specs() -> list[tuple]:
    station_coords = {code: (lat, lng) for code, _name, lat, lng in STATION_SPECS}
    specs: list[tuple] = list(FLAGSHIP_TRAIN_SPECS)
    seen_numbers = {row[0] for row in specs}

    regional_routes = [
        "R-GYEONGGI-NORTH",
        "R-GYEONGGI-EAST",
        "R-GYEONGGI-SOUTH",
        "R-GYEONGGI-WEST",
        "R-GYEONGGI-CENTER",
        "R-METRO-INCHEON",
        "R-GANGWON-INLAND",
        "R-GANGWON-COAST",
        "R-CHUNGCHEONG",
        "R-CHUNGCHEONG-WEST",
        "R-HONAM-EAST",
        "R-JEOLLA-CROSS",
        "R-GYEONGSANG-SOUTH",
        "R-GYEONGSANG-WEST",
        "R-DONGHAE",
        "R-METRO-YEONGNAM",
    ]

    prefix_map = {
        "R-GYEONGGI-NORTH": ("SM-31", 120),
        "R-GYEONGGI-EAST": ("SM-32", 160),
        "R-GYEONGGI-SOUTH": ("SM-33", 200),
        "R-GYEONGGI-WEST": ("SM-34", 240),
        "R-GYEONGGI-CENTER": ("SM-35", 280),
        "R-METRO-INCHEON": ("SM-36", 320),
        "R-GANGWON-INLAND": ("SM-37", 360),
        "R-GANGWON-COAST": ("SM-38", 400),
        "R-CHUNGCHEONG": ("SM-39", 440),
        "R-CHUNGCHEONG-WEST": ("SM-40", 480),
        "R-HONAM-EAST": ("SM-41", 520),
        "R-JEOLLA-CROSS": ("SM-42", 560),
        "R-GYEONGSANG-SOUTH": ("SM-43", 600),
        "R-GYEONGSANG-WEST": ("SM-44", 640),
        "R-DONGHAE": ("SM-45", 680),
        "R-METRO-YEONGNAM": ("SM-46", 720),
    }

    route_lookup = {code: (name, stations) for code, name, stations in ROUTE_SPECS}
    for route_code in regional_routes:
        route_name, station_codes = route_lookup[route_code]
        prefix, offset_base = prefix_map[route_code]
        generated = _segment_train_specs(
            station_coords,
            route_code,
            route_name,
            station_codes,
            train_type="METRO",
            number_prefix=prefix,
            offset_base=offset_base,
        )
        for row in generated:
            if row[0] in seen_numbers:
                continue
            seen_numbers.add(row[0])
            specs.append(row)

    backbone_routes = [
        ("R-SEOUL-01", "SM-51", 760, "METRO"),
        ("R-EAST-AXIS", "SM-52", 800, "HIGHSPEED"),
        ("R-SEOUL-BUSAN", "SM-53", 840, "HIGHSPEED"),
        ("R-HONAM", "SM-54", 880, "HIGHSPEED"),
        ("R-SOUTH-CROSS", "SM-55", 920, "HIGHSPEED"),
        ("R-JEJU-LINK", "SM-56", 960, "HIGHSPEED"),
        ("R-SEOUL-JEJU", "SM-57", 1000, "HIGHSPEED"),
    ]
    for route_code, prefix, offset_base, train_type in backbone_routes:
        route_name, station_codes = route_lookup[route_code]
        generated = _segment_train_specs(
            station_coords,
            route_code,
            route_name,
            station_codes,
            train_type=train_type,
            number_prefix=prefix,
            offset_base=offset_base,
        )
        for row in generated:
            if row[0] in seen_numbers:
                continue
            seen_numbers.add(row[0])
            specs.append(row)

    return specs


def iter_train_specs() -> Iterable[tuple]:
    return build_train_specs()
