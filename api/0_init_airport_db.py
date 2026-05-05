import os
import sqlite3
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import APIRouter

router = APIRouter()

load_dotenv()

# Airport DB lives in a separate file alongside the main DB
_main_db_path = os.getenv("DB_PATH", "database/main.db")
AIRPORT_DB_PATH = os.path.join(os.path.dirname(_main_db_path), "airport.db")
os.makedirs(os.path.dirname(AIRPORT_DB_PATH), exist_ok=True)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _col_exists(cursor, table: str, col: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cursor.fetchall())


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------

AIRLINES = [
    ("CA", "Air China", "中国国际航空", "Beijing", "CAAC", "B-xxxx", "Star Alliance"),
    ("MU", "China Eastern Airlines", "中国东方航空", "Shanghai", "CAAC", "B-xxxx", "SkyTeam"),
    ("CZ", "China Southern Airlines", "中国南方航空", "Guangzhou", "CAAC", "B-xxxx", "SkyTeam"),
    ("HU", "Hainan Airlines", "海南航空", "Haikou", "CAAC", "B-xxxx", None),
    ("3U", "Sichuan Airlines", "四川航空", "Chengdu", "CAAC", "B-xxxx", None),
    ("AA", "American Airlines", "美国航空", "Fort Worth", "FAA", "N-xxxx", "Oneworld"),
    ("DL", "Delta Air Lines", "达美航空", "Atlanta", "FAA", "N-xxxx", "SkyTeam"),
    ("UA", "United Airlines", "联合航空", "Chicago", "FAA", "N-xxxx", "Star Alliance"),
    ("BA", "British Airways", "英国航空", "London", "CAA", "G-xxxx", "Oneworld"),
    ("EK", "Emirates", "阿联酋航空", "Dubai", "GCAA", "A6-xxxx", None),
    ("SQ", "Singapore Airlines", "新加坡航空", "Singapore", "CAAS", "9V-xxxx", "Star Alliance"),
    ("JL", "Japan Airlines", "日本航空", "Tokyo", "JCAB", "JA-xxxx", "Oneworld"),
]

AIRPORTS = [
    ("PEK", "Beijing Capital International Airport", "北京首都国际机场", "Beijing", "China", "CN",
     40.0801, 116.5846, 35, "Asia/Shanghai", 3, "ZBAA", True),
    ("PVG", "Shanghai Pudong International Airport", "上海浦东国际机场", "Shanghai", "China", "CN",
     31.1443, 121.8083, 4, "Asia/Shanghai", 2, "ZSPD", True),
    ("CAN", "Guangzhou Baiyun International Airport", "广州白云国际机场", "Guangzhou", "China", "CN",
     23.3924, 113.2990, 15, "Asia/Shanghai", 2, "ZGGG", True),
    ("CTU", "Chengdu Tianfu International Airport", "成都天府国际机场", "Chengdu", "China", "CN",
     30.3124, 104.4441, 449, "Asia/Shanghai", 2, "ZUTF", True),
    ("SHA", "Shanghai Hongqiao International Airport", "上海虹桥国际机场", "Shanghai", "China", "CN",
     31.1979, 121.3364, 3, "Asia/Shanghai", 2, "ZSSS", False),
    ("LAX", "Los Angeles International Airport", "洛杉矶国际机场", "Los Angeles", "USA", "US",
     33.9425, -118.4081, 38, "America/Los_Angeles", 9, "KLAX", True),
    ("JFK", "John F. Kennedy International Airport", "约翰·肯尼迪国际机场", "New York", "USA", "US",
     40.6413, -73.7781, 13, "America/New_York", 6, "KJFK", True),
    ("LHR", "London Heathrow Airport", "伦敦希思罗机场", "London", "UK", "GB",
     51.4775, -0.4614, 25, "Europe/London", 5, "EGLL", True),
    ("DXB", "Dubai International Airport", "迪拜国际机场", "Dubai", "UAE", "AE",
     25.2532, 55.3657, 19, "Asia/Dubai", 3, "OMDB", True),
    ("SIN", "Singapore Changi Airport", "新加坡樟宜机场", "Singapore", "Singapore", "SG",
     1.3644, 103.9915, 7, "Asia/Singapore", 4, "WSSS", True),
    ("NRT", "Tokyo Narita International Airport", "东京成田国际机场", "Tokyo", "Japan", "JP",
     35.7653, 140.3856, 41, "Asia/Tokyo", 3, "RJAA", True),
    ("HKG", "Hong Kong International Airport", "香港国际机场", "Hong Kong", "China", "CN",
     22.3080, 113.9185, 9, "Asia/Hong_Kong", 2, "VHHH", True),
]

AIRCRAFT_TYPES = [
    ("B737-800", "Boeing", "737-800", 162, 12, 150, 162, 5765, 2, 3, 189, 73500),
    ("B737-MAX8", "Boeing", "737 MAX 8", 172, 16, 156, 172, 6570, 2, 3, 189, 82200),
    ("B747-400", "Boeing", "747-400", 416, 58, 267, 91, 13490, 4, 4, 568, 396890),
    ("B777-300ER", "Boeing", "777-300ER", 396, 60, 232, 104, 13650, 2, 3, 550, 351500),
    ("B787-9", "Boeing", "787-9", 296, 28, 180, 88, 14140, 2, 3, 420, 254011),
    ("A320neo", "Airbus", "A320neo", 165, 12, 153, 0, 6300, 2, 3, 194, 79000),
    ("A330-300", "Airbus", "A330-300", 300, 36, 208, 56, 11750, 2, 3, 440, 242000),
    ("A350-900", "Airbus", "A350-900", 369, 42, 231, 96, 15000, 2, 3, 440, 280000),
    ("A380-800", "Airbus", "A380-800", 555, 98, 360, 97, 15200, 4, 4, 853, 575000),
    ("E190", "Embraer", "E190", 96, 0, 96, 0, 4537, 2, 2, 106, 51800),
]

TERMINALS = {
    "PEK": [("T1", "Terminal 1", "Domestic", 20, 5, True),
            ("T2", "Terminal 2", "International", 35, 8, True),
            ("T3", "Terminal 3", "Mixed", 60, 15, True)],
    "PVG": [("T1", "Terminal 1", "Domestic", 40, 10, True),
            ("T2", "Terminal 2", "International", 80, 20, True)],
    "CAN": [("T1", "Terminal 1", "International", 70, 18, True),
            ("T2", "Terminal 2", "Domestic", 50, 12, True)],
    "CTU": [("T1", "Terminal 1", "Mixed", 60, 15, True),
            ("T2", "Terminal 2", "International", 40, 10, True)],
    "SHA": [("T1", "Terminal 1", "Domestic", 20, 5, True),
            ("T2", "Terminal 2", "Domestic", 30, 8, True)],
    "LAX": [("1", "Terminal 1", "Domestic", 18, 5, True),
            ("2", "Terminal 2", "International", 22, 7, True),
            ("B", "Terminal B", "Mixed", 40, 10, True),
            ("TBIT", "Tom Bradley International Terminal", "International", 140, 35, True)],
    "JFK": [("1", "Terminal 1", "International", 50, 13, True),
            ("4", "Terminal 4", "International", 80, 20, True),
            ("5", "Terminal 5 (JetBlue)", "Domestic", 26, 7, True),
            ("8", "Terminal 8", "Mixed", 36, 9, True)],
    "LHR": [("2", "Terminal 2", "International", 60, 15, True),
            ("3", "Terminal 3", "International", 55, 14, True),
            ("4", "Terminal 4", "International", 38, 10, True),
            ("5", "Terminal 5", "International", 90, 24, True)],
    "DXB": [("1", "Terminal 1", "International", 98, 25, True),
            ("2", "Terminal 2", "Low-cost", 13, 4, True),
            ("3", "Terminal 3", "Emirates Only", 180, 46, True)],
    "SIN": [("1", "Terminal 1", "International", 80, 20, True),
            ("2", "Terminal 2", "International", 75, 19, True),
            ("3", "Terminal 3", "International", 110, 28, True),
            ("4", "Terminal 4", "Low-cost", 44, 11, True)],
    "NRT": [("1", "Terminal 1", "International", 50, 12, True),
            ("2", "Terminal 2", "International", 50, 12, True),
            ("3", "Terminal 3", "Low-cost", 12, 3, True)],
    "HKG": [("1", "Terminal 1", "International", 70, 18, True),
            ("2", "Terminal 2", "Low-cost", 20, 5, True)],
}

GATE_TYPES = ["Jetbridge", "Remote", "Bus Gate"]

RUNWAY_SURFACES = ["Asphalt", "Concrete"]

AMENITY_CATEGORIES = ["Dining", "Retail", "Lounge", "Banking", "Medical", "Religious", "Children", "Connectivity"]


def _random_date(start_year=2020, end_year=2026):
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 1, 1)
    delta = end - start
    return (start + timedelta(days=random.randint(0, delta.days))).strftime("%Y-%m-%d")


def _random_time():
    h = random.randint(0, 23)
    m = random.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55])
    return f"{h:02d}:{m:02d}"


def _flight_number(iata: str, num: int) -> str:
    return f"{iata}{num:04d}"


# ---------------------------------------------------------------------------
# Main init function
# ---------------------------------------------------------------------------

def init_airport_db():
    conn = sqlite3.connect(AIRPORT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")

    # -----------------------------------------------------------------------
    # 1. airlines
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS airlines (
            airline_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            iata_code       TEXT UNIQUE NOT NULL,
            icao_code       TEXT,
            name_en         TEXT NOT NULL,
            name_zh         TEXT,
            headquarters    TEXT,
            country         TEXT,
            regulator       TEXT,
            reg_prefix      TEXT,
            alliance        TEXT,
            founded_year    INTEGER,
            fleet_size      INTEGER,
            destinations    INTEGER,
            is_active       INTEGER DEFAULT 1,
            website         TEXT,
            hotline         TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # -----------------------------------------------------------------------
    # 2. airports
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS airports (
            airport_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            iata_code       TEXT UNIQUE NOT NULL,
            icao_code       TEXT UNIQUE,
            name_en         TEXT NOT NULL,
            name_zh         TEXT,
            city            TEXT,
            country         TEXT,
            country_code    TEXT,
            latitude        REAL,
            longitude       REAL,
            elevation_m     INTEGER,
            timezone        TEXT,
            terminal_count  INTEGER,
            is_international INTEGER DEFAULT 1,
            annual_passengers INTEGER,
            annual_flights  INTEGER,
            hub_airlines    TEXT,
            website         TEXT,
            phone           TEXT,
            opened_year     INTEGER,
            runway_count    INTEGER,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # -----------------------------------------------------------------------
    # 3. terminals
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS terminals (
            terminal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            terminal_code   TEXT NOT NULL,
            name_en         TEXT,
            terminal_type   TEXT,
            gate_count      INTEGER,
            check_in_desks  INTEGER,
            has_transit_hotel INTEGER DEFAULT 0,
            has_lounge      INTEGER DEFAULT 1,
            has_duty_free   INTEGER DEFAULT 1,
            is_open         INTEGER DEFAULT 1,
            opening_year    INTEGER,
            area_sqm        INTEGER,
            floor_count     INTEGER DEFAULT 3,
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(airport_iata, terminal_code),
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 4. gates
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gates (
            gate_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            terminal_code   TEXT NOT NULL,
            gate_code       TEXT NOT NULL,
            gate_type       TEXT,
            max_aircraft_size TEXT,
            has_jetbridge   INTEGER DEFAULT 1,
            is_international INTEGER DEFAULT 0,
            is_active       INTEGER DEFAULT 1,
            last_maintained TEXT,
            UNIQUE(airport_iata, terminal_code, gate_code),
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 5. runways
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS runways (
            runway_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            runway_designator TEXT NOT NULL,
            length_m        INTEGER,
            width_m         INTEGER,
            surface         TEXT,
            heading_true    REAL,
            ils_equipped    INTEGER DEFAULT 1,
            cat_ils         TEXT,
            lighting        TEXT,
            pcn_rating      INTEGER,
            is_active       INTEGER DEFAULT 1,
            last_resurfaced TEXT,
            UNIQUE(airport_iata, runway_designator),
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 6. aircraft_types
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aircraft_types (
            type_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            iata_type_code  TEXT UNIQUE NOT NULL,
            manufacturer    TEXT,
            model           TEXT,
            total_seats     INTEGER,
            business_seats  INTEGER,
            economy_seats   INTEGER,
            premium_economy_seats INTEGER,
            range_km        INTEGER,
            engine_count    INTEGER,
            seats_per_row   INTEGER,
            max_capacity    INTEGER,
            mtow_kg         INTEGER,
            is_widebody     INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # -----------------------------------------------------------------------
    # 7. aircraft_fleet  (individual planes per airline)
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aircraft_fleet (
            fleet_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            registration    TEXT UNIQUE NOT NULL,
            airline_iata    TEXT NOT NULL,
            type_code       TEXT NOT NULL,
            manufacture_date TEXT,
            delivery_date   TEXT,
            seat_config     TEXT,
            current_airport TEXT,
            status          TEXT DEFAULT 'Active',
            last_maintenance TEXT,
            next_maintenance TEXT,
            total_flight_hours INTEGER DEFAULT 0,
            total_cycles    INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (airline_iata) REFERENCES airlines(iata_code),
            FOREIGN KEY (type_code)    REFERENCES aircraft_types(iata_type_code),
            FOREIGN KEY (current_airport) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 8. routes
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            route_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            airline_iata    TEXT NOT NULL,
            origin_iata     TEXT NOT NULL,
            destination_iata TEXT NOT NULL,
            flight_number   TEXT NOT NULL,
            distance_km     INTEGER,
            flight_duration_min INTEGER,
            frequency_weekly INTEGER DEFAULT 7,
            aircraft_type   TEXT,
            is_codeshare    INTEGER DEFAULT 0,
            codeshare_partner TEXT,
            effective_from  TEXT,
            effective_to    TEXT,
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(airline_iata, flight_number),
            FOREIGN KEY (airline_iata)      REFERENCES airlines(iata_code),
            FOREIGN KEY (origin_iata)       REFERENCES airports(iata_code),
            FOREIGN KEY (destination_iata)  REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 9. flights  (scheduled individual flight instances)
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flights (
            flight_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_number       TEXT NOT NULL,
            airline_iata        TEXT NOT NULL,
            origin_iata         TEXT NOT NULL,
            destination_iata    TEXT NOT NULL,
            scheduled_departure TEXT NOT NULL,
            scheduled_arrival   TEXT NOT NULL,
            actual_departure    TEXT,
            actual_arrival      TEXT,
            status              TEXT DEFAULT 'Scheduled',
            departure_terminal  TEXT,
            departure_gate      TEXT,
            arrival_terminal    TEXT,
            arrival_gate        TEXT,
            aircraft_registration TEXT,
            aircraft_type       TEXT,
            total_seats         INTEGER,
            seats_sold          INTEGER DEFAULT 0,
            load_factor         REAL,
            delay_minutes       INTEGER DEFAULT 0,
            delay_reason        TEXT,
            is_cancelled        INTEGER DEFAULT 0,
            cancellation_reason TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (airline_iata)   REFERENCES airlines(iata_code),
            FOREIGN KEY (origin_iata)    REFERENCES airports(iata_code),
            FOREIGN KEY (destination_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 10. passengers
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            pnr             TEXT UNIQUE NOT NULL,
            first_name      TEXT NOT NULL,
            last_name       TEXT NOT NULL,
            gender          TEXT,
            date_of_birth   TEXT,
            nationality     TEXT,
            passport_number TEXT,
            passport_expiry TEXT,
            email           TEXT,
            phone           TEXT,
            frequent_flyer_id TEXT,
            frequent_flyer_airline TEXT,
            tier_level      TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    # -----------------------------------------------------------------------
    # 11. bookings
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            pnr             TEXT NOT NULL,
            flight_id       INTEGER NOT NULL,
            passenger_id    INTEGER NOT NULL,
            cabin_class     TEXT DEFAULT 'Economy',
            seat_number     TEXT,
            ticket_price    REAL,
            currency        TEXT DEFAULT 'CNY',
            booking_channel TEXT,
            booking_time    TEXT DEFAULT (datetime('now')),
            check_in_status TEXT DEFAULT 'Not Checked In',
            boarding_pass_issued INTEGER DEFAULT 0,
            baggage_allowance_kg INTEGER DEFAULT 23,
            extra_baggage_kg INTEGER DEFAULT 0,
            meal_preference TEXT,
            special_assistance TEXT,
            is_cancelled    INTEGER DEFAULT 0,
            FOREIGN KEY (flight_id)    REFERENCES flights(flight_id),
            FOREIGN KEY (passenger_id) REFERENCES passengers(passenger_id)
        )
    """)

    # -----------------------------------------------------------------------
    # 12. baggage
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS baggage (
            baggage_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id      INTEGER NOT NULL,
            tag_number      TEXT UNIQUE NOT NULL,
            weight_kg       REAL,
            type            TEXT DEFAULT 'Checked',
            status          TEXT DEFAULT 'Checked In',
            last_location   TEXT,
            last_scan_time  TEXT,
            is_oversize     INTEGER DEFAULT 0,
            is_lost         INTEGER DEFAULT 0,
            claim_filed     INTEGER DEFAULT 0,
            FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
        )
    """)

    # -----------------------------------------------------------------------
    # 13. check_in_counters
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS check_in_counters (
            counter_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            terminal_code   TEXT NOT NULL,
            counter_number  TEXT NOT NULL,
            airline_iata    TEXT,
            counter_type    TEXT DEFAULT 'Standard',
            is_open         INTEGER DEFAULT 1,
            queue_length    INTEGER DEFAULT 0,
            open_time       TEXT,
            close_time      TEXT,
            UNIQUE(airport_iata, terminal_code, counter_number),
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 14. security_checkpoints
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_checkpoints (
            checkpoint_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            terminal_code   TEXT NOT NULL,
            checkpoint_name TEXT,
            lane_count      INTEGER DEFAULT 4,
            open_lanes      INTEGER DEFAULT 4,
            is_priority     INTEGER DEFAULT 0,
            avg_wait_min    INTEGER DEFAULT 10,
            is_open         INTEGER DEFAULT 1,
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 15. lounges
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lounges (
            lounge_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            terminal_code   TEXT NOT NULL,
            lounge_name     TEXT NOT NULL,
            operator        TEXT,
            capacity        INTEGER,
            access_policy   TEXT,
            has_shower      INTEGER DEFAULT 1,
            has_spa         INTEGER DEFAULT 0,
            has_restaurant  INTEGER DEFAULT 1,
            has_bar         INTEGER DEFAULT 1,
            has_wifi        INTEGER DEFAULT 1,
            has_sleeping_pods INTEGER DEFAULT 0,
            open_time       TEXT DEFAULT '05:00',
            close_time      TEXT DEFAULT '23:00',
            is_open         INTEGER DEFAULT 1,
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 16. amenities
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS amenities (
            amenity_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            terminal_code   TEXT NOT NULL,
            name            TEXT NOT NULL,
            category        TEXT,
            floor           INTEGER DEFAULT 1,
            location_desc   TEXT,
            open_time       TEXT,
            close_time      TEXT,
            is_open         INTEGER DEFAULT 1,
            phone           TEXT,
            website         TEXT,
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 17. parking
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parking (
            parking_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            lot_name        TEXT NOT NULL,
            lot_type        TEXT,
            total_spaces    INTEGER,
            available_spaces INTEGER,
            price_per_hour  REAL,
            price_per_day   REAL,
            currency        TEXT DEFAULT 'CNY',
            distance_to_terminal TEXT,
            has_ev_charging INTEGER DEFAULT 0,
            has_disabled_spaces INTEGER DEFAULT 1,
            is_covered      INTEGER DEFAULT 0,
            is_open         INTEGER DEFAULT 1,
            UNIQUE(airport_iata, lot_name),
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 18. ground_transport
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ground_transport (
            transport_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            transport_type  TEXT NOT NULL,
            operator        TEXT,
            destination     TEXT,
            duration_min    INTEGER,
            price_range     TEXT,
            currency        TEXT DEFAULT 'CNY',
            frequency_min   INTEGER,
            operating_hours TEXT,
            terminal_code   TEXT,
            booking_required INTEGER DEFAULT 0,
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 19. weather_conditions  (current + forecast per airport)
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather_conditions (
            weather_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            airport_iata    TEXT NOT NULL,
            recorded_at     TEXT NOT NULL,
            temperature_c   REAL,
            feels_like_c    REAL,
            humidity_pct    INTEGER,
            wind_speed_kmh  REAL,
            wind_direction  TEXT,
            visibility_km   REAL,
            weather_desc    TEXT,
            is_vmc          INTEGER DEFAULT 1,
            ceiling_ft      INTEGER,
            precipitation_mm REAL DEFAULT 0,
            FOREIGN KEY (airport_iata) REFERENCES airports(iata_code)
        )
    """)

    # -----------------------------------------------------------------------
    # 20. flight_prices  (fare buckets per route)
    # -----------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flight_prices (
            price_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id        INTEGER NOT NULL,
            cabin_class     TEXT NOT NULL,
            fare_basis      TEXT,
            price           REAL NOT NULL,
            currency        TEXT DEFAULT 'CNY',
            seats_available INTEGER,
            valid_from      TEXT,
            valid_to        TEXT,
            refundable      INTEGER DEFAULT 0,
            changeable      INTEGER DEFAULT 1,
            FOREIGN KEY (route_id) REFERENCES routes(route_id)
        )
    """)

    conn.commit()

    # -----------------------------------------------------------------------
    # Seed data
    # -----------------------------------------------------------------------
    random.seed(42)

    # airlines
    for row in AIRLINES:
        iata, name_en, name_zh, hq, reg, reg_pfx, alliance = row
        cursor.execute("""
            INSERT OR IGNORE INTO airlines
            (iata_code, name_en, name_zh, headquarters, regulator, reg_prefix, alliance,
             founded_year, fleet_size, destinations, website, hotline)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (iata, name_en, name_zh, hq, reg, reg_pfx, alliance,
              random.randint(1950, 2005), random.randint(50, 800), random.randint(30, 200),
              f"https://www.{name_en.lower().replace(' ', '')}.com",
              f"+86-{random.randint(400,499)}-{random.randint(1000000,9999999)}"))

    # airports
    for row in AIRPORTS:
        (iata, name_en, name_zh, city, country, cc,
         lat, lon, elev, tz, term_cnt, icao, is_intl) = row
        cursor.execute("""
            INSERT OR IGNORE INTO airports
            (iata_code, icao_code, name_en, name_zh, city, country, country_code,
             latitude, longitude, elevation_m, timezone, terminal_count, is_international,
             annual_passengers, annual_flights, opened_year, runway_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (iata, icao, name_en, name_zh, city, country, cc,
              lat, lon, elev, tz, term_cnt, 1 if is_intl else 0,
              random.randint(20_000_000, 95_000_000),
              random.randint(100_000, 600_000),
              random.randint(1955, 2005),
              random.randint(2, 5)))

    # terminals
    for airport_iata, term_list in TERMINALS.items():
        for t in term_list:
            tcode, tname, ttype, gates, desks, is_open = t
            cursor.execute("""
                INSERT OR IGNORE INTO terminals
                (airport_iata, terminal_code, name_en, terminal_type, gate_count,
                 check_in_desks, has_transit_hotel, has_lounge, has_duty_free, is_open,
                 opening_year, area_sqm, floor_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (airport_iata, tcode, tname, ttype, gates, desks,
                  random.randint(0, 1), 1, 1, 1 if is_open else 0,
                  random.randint(1980, 2020),
                  random.randint(50_000, 500_000),
                  random.randint(3, 5)))
            # gates
            for g in range(1, gates + 1):
                gate_code = f"{tcode}{g}"
                cursor.execute("""
                    INSERT OR IGNORE INTO gates
                    (airport_iata, terminal_code, gate_code, gate_type, max_aircraft_size,
                     has_jetbridge, is_international, is_active, last_maintained)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (airport_iata, tcode, gate_code,
                      random.choice(GATE_TYPES),
                      random.choice(["Narrow-body", "Wide-body", "Any"]),
                      random.randint(0, 1), random.randint(0, 1), 1,
                      _random_date(2022, 2026)))

    # runways
    runway_configs = {
        "PEK": [("01/19", 3800, 60), ("18L/36R", 3200, 60), ("18R/36L", 3800, 60)],
        "PVG": [("16L/34R", 4000, 60), ("17R/35L", 3400, 60)],
        "CAN": [("02L/20R", 3800, 60), ("02R/20L", 3600, 60)],
        "CTU": [("02L/20R", 4000, 60), ("02R/20L", 3800, 60)],
        "SHA": [("18L/36R", 3300, 60), ("18R/36L", 3300, 60)],
        "LAX": [("06L/24R", 3685, 61), ("06R/24L", 3382, 46),
                ("07L/25R", 3382, 46), ("07R/25L", 3685, 61)],
        "JFK": [("04L/22R", 2560, 46), ("04R/22L", 3460, 46),
                ("13L/31R", 4442, 61), ("13R/31L", 3048, 46)],
        "LHR": [("09L/27R", 3902, 50), ("09R/27L", 3658, 50)],
        "DXB": [("12L/30R", 4000, 60), ("12R/30L", 4000, 60)],
        "SIN": [("02C/20C", 4000, 60), ("02L/20R", 3800, 60),
                ("02R/20L", 3800, 60)],
        "NRT": [("16L/34R", 4000, 60), ("16R/34L", 2500, 60)],
        "HKG": [("07L/25R", 3800, 60), ("07R/25L", 3800, 60)],
    }
    for airport_iata, rwys in runway_configs.items():
        for rwy_des, length, width in rwys:
            cursor.execute("""
                INSERT OR IGNORE INTO runways
                (airport_iata, runway_designator, length_m, width_m, surface,
                 heading_true, ils_equipped, cat_ils, lighting, pcn_rating,
                 is_active, last_resurfaced)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (airport_iata, rwy_des, length, width,
                  random.choice(RUNWAY_SURFACES),
                  random.uniform(0, 360),
                  1, random.choice(["CAT I", "CAT II", "CAT III"]),
                  "HIRL", random.randint(60, 100),
                  1, _random_date(2015, 2024)))

    # aircraft types
    for row in AIRCRAFT_TYPES:
        (tcode, mfr, model, total, biz, eco, pe, rng,
         engines, seats_row, max_cap, mtow) = row
        cursor.execute("""
            INSERT OR IGNORE INTO aircraft_types
            (iata_type_code, manufacturer, model, total_seats, business_seats,
             economy_seats, premium_economy_seats, range_km, engine_count,
             seats_per_row, max_capacity, mtow_kg, is_widebody)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tcode, mfr, model, total, biz, eco, pe, rng, engines,
              seats_row, max_cap, mtow, 1 if seats_row >= 3 else 0))

    # aircraft fleet  (5 aircraft per airline)
    airline_types = {
        "CA": ["B737-800", "B777-300ER", "B787-9", "A350-900"],
        "MU": ["B737-800", "A320neo", "A330-300", "B777-300ER"],
        "CZ": ["B737-800", "A320neo", "A330-300", "B777-300ER"],
        "HU": ["B737-800", "A330-300", "B787-9"],
        "3U": ["A320neo", "A330-300"],
        "AA": ["B737-800", "B777-300ER", "B787-9"],
        "DL": ["B737-800", "A330-300", "B777-300ER"],
        "UA": ["B737-800", "B777-300ER", "B787-9"],
        "BA": ["B777-300ER", "B787-9", "A350-900"],
        "EK": ["A380-800", "B777-300ER", "A350-900"],
        "SQ": ["A350-900", "A380-800", "B777-300ER"],
        "JL": ["B737-800", "B777-300ER", "B787-9"],
    }
    airports_list = [a[0] for a in AIRPORTS]
    for airline_iata, types in airline_types.items():
        for i in range(6):
            ac_type = types[i % len(types)]
            reg = f"{airline_iata}-{random.randint(1000,9999)}"
            cursor.execute("""
                INSERT OR IGNORE INTO aircraft_fleet
                (registration, airline_iata, type_code, manufacture_date, delivery_date,
                 current_airport, status, last_maintenance, next_maintenance,
                 total_flight_hours, total_cycles)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (reg, airline_iata, ac_type,
                  _random_date(2010, 2023), _random_date(2010, 2023),
                  random.choice(airports_list),
                  random.choice(["Active", "Active", "Active", "Maintenance", "Reserve"]),
                  _random_date(2024, 2026), _random_date(2026, 2027),
                  random.randint(1000, 50000), random.randint(500, 20000)))

    # routes & flights
    route_pairs = [
        ("CA", "PEK", "PVG", 1080, 130), ("CA", "PEK", "CAN", 1890, 180),
        ("CA", "PEK", "LAX", 10000, 660), ("CA", "PEK", "LHR", 8160, 540),
        ("MU", "PVG", "PEK", 1080, 130), ("MU", "PVG", "CAN", 1430, 160),
        ("MU", "PVG", "NRT", 1760, 130), ("MU", "PVG", "SIN", 5330, 360),
        ("CZ", "CAN", "PEK", 1890, 180), ("CZ", "CAN", "PVG", 1430, 160),
        ("CZ", "CAN", "SIN", 3310, 240), ("CZ", "CAN", "LHR", 9530, 600),
        ("HU", "PEK", "CTU", 1510, 155), ("3U", "CTU", "PVG", 1700, 160),
        ("AA", "LAX", "JFK", 4490, 310), ("AA", "LAX", "LHR", 8750, 570),
        ("DL", "JFK", "LAX", 4490, 310), ("DL", "JFK", "LHR", 5570, 415),
        ("UA", "LAX", "NRT", 8800, 590), ("BA", "LHR", "JFK", 5570, 415),
        ("BA", "LHR", "DXB", 5490, 400), ("EK", "DXB", "LHR", 5490, 400),
        ("EK", "DXB", "SIN", 5840, 420), ("SQ", "SIN", "LHR", 10840, 740),
        ("SQ", "SIN", "NRT", 5320, 360), ("JL", "NRT", "LHR", 9560, 650),
        ("JL", "NRT", "PEK", 2090, 185), ("CA", "PEK", "HKG", 2010, 185),
        ("MU", "PVG", "HKG", 1270, 140), ("CZ", "CAN", "HKG", 130, 30),
    ]
    route_id_map = {}
    for idx, (al, orig, dest, dist, dur) in enumerate(route_pairs, 1):
        fn = _flight_number(al, 100 + idx * 3)
        ac_type = random.choice(airline_types.get(al, ["B737-800"]))
        cursor.execute("""
            INSERT OR IGNORE INTO routes
            (airline_iata, origin_iata, destination_iata, flight_number, distance_km,
             flight_duration_min, frequency_weekly, aircraft_type, is_active)
            VALUES (?,?,?,?,?,?,?,?,1)
        """, (al, orig, dest, fn, dist, dur, random.randint(5, 14), ac_type))
        cursor.execute("SELECT route_id FROM routes WHERE airline_iata=? AND flight_number=?", (al, fn))
        row = cursor.fetchone()
        if row:
            route_id_map[fn] = (row[0], al, orig, dest, fn, dur)

    # flights (3 upcoming per route)
    statuses = ["Scheduled", "Scheduled", "Scheduled", "On Time", "Delayed", "Boarding", "Departed"]
    delay_reasons = ["Weather", "Technical", "Air Traffic Control", "Crew", "Late Aircraft", None]
    base_date = datetime(2026, 5, 5)
    for fn, (rid, al, orig, dest, flight_num, dur) in route_id_map.items():
        for day_offset in range(3):
            dep_dt = base_date + timedelta(days=day_offset, hours=random.randint(0, 23), minutes=random.choice([0, 15, 30, 45]))
            arr_dt = dep_dt + timedelta(minutes=dur)
            status = random.choice(statuses)
            delay = random.choice([0, 0, 0, 15, 30, 60, 90]) if status == "Delayed" else 0
            t_orig = TERMINALS.get(orig, [("T1",)])[0][0]
            t_dest = TERMINALS.get(dest, [("T1",)])[0][0]
            ac_type = random.choice(airline_types.get(al, ["B737-800"]))
            # find seats
            cursor.execute("SELECT total_seats FROM aircraft_types WHERE iata_type_code=?", (ac_type,))
            seat_row = cursor.fetchone()
            total_s = seat_row[0] if seat_row else 180
            sold = random.randint(int(total_s * 0.4), total_s)
            cursor.execute("""
                INSERT INTO flights
                (flight_number, airline_iata, origin_iata, destination_iata,
                 scheduled_departure, scheduled_arrival, status,
                 departure_terminal, departure_gate, arrival_terminal, arrival_gate,
                 aircraft_type, total_seats, seats_sold, load_factor,
                 delay_minutes, delay_reason, is_cancelled)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            """, (flight_num, al, orig, dest,
                  dep_dt.strftime("%Y-%m-%d %H:%M"),
                  arr_dt.strftime("%Y-%m-%d %H:%M"),
                  status, t_orig,
                  f"{t_orig}{random.randint(1,10)}",
                  t_dest,
                  f"{t_dest}{random.randint(1,10)}",
                  ac_type, total_s, sold,
                  round(sold / total_s, 2),
                  delay, random.choice(delay_reasons) if delay > 0 else None))

    # lounges
    lounge_names = ["First Class Lounge", "Business Lounge", "Priority Pass Lounge",
                    "Star Alliance Lounge", "SkyTeam Lounge", "Oneworld Lounge"]
    for airport_iata, term_list in TERMINALS.items():
        for t in term_list:
            tcode = t[0]
            lname = random.choice(lounge_names)
            cursor.execute("""
                INSERT OR IGNORE INTO lounges
                (airport_iata, terminal_code, lounge_name, operator, capacity,
                 access_policy, has_shower, has_spa, has_restaurant, has_bar,
                 has_wifi, has_sleeping_pods, open_time, close_time, is_open)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (airport_iata, tcode, lname,
                  random.choice(["Airport Authority", "Airline", "Plaza Premium", "Priority Pass"]),
                  random.randint(50, 300),
                  random.choice(["Business/First Class Passengers", "Priority Pass Members", "All Passengers (Fee)"]),
                  1, random.randint(0, 1), 1, 1, 1, random.randint(0, 1),
                  "05:30", "23:30", 1))

    # amenities
    dining_options = ["McDonald's", "Starbucks", "Chinese Restaurant", "Sushi Bar", "Pizza Hut",
                      "Local Cuisine", "Seafood Restaurant", "Noodle Shop", "Burger King", "KFC"]
    retail_options = ["Duty Free Shop", "Bookstore", "Electronics Store", "Fashion Boutique",
                      "Souvenir Shop", "Pharmacy", "Newsstand", "Jewelry Store"]
    service_options = ["Bank / ATM", "Currency Exchange", "Medical Center", "Prayer Room",
                       "Children's Play Area", "Wifi Zone", "Business Center", "Luggage Storage"]
    for airport_iata, term_list in TERMINALS.items():
        for t in term_list:
            tcode = t[0]
            for name in random.sample(dining_options, 3):
                cursor.execute("""
                    INSERT INTO amenities (airport_iata, terminal_code, name, category, floor,
                    location_desc, open_time, close_time, is_open)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (airport_iata, tcode, name, "Dining", random.randint(1, 3),
                      f"Near Gate {tcode}{random.randint(1,10)}", "06:00", "22:00", 1))
            for name in random.sample(retail_options, 2):
                cursor.execute("""
                    INSERT INTO amenities (airport_iata, terminal_code, name, category, floor,
                    location_desc, open_time, close_time, is_open)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (airport_iata, tcode, name, "Retail", random.randint(1, 3),
                      f"Departure Hall {tcode}", "07:00", "21:00", 1))
            for name in random.sample(service_options, 2):
                cursor.execute("""
                    INSERT INTO amenities (airport_iata, terminal_code, name, category, floor,
                    location_desc, open_time, close_time, is_open)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (airport_iata, tcode, name, "Services", 1,
                      f"Terminal {tcode} Ground Floor", "00:00", "23:59", 1))

    # parking
    lot_types = ["Short-term", "Long-term", "P+R", "VIP"]
    for airport_iata in [a[0] for a in AIRPORTS]:
        for lt in lot_types:
            cursor.execute("""
                INSERT OR IGNORE INTO parking
                (airport_iata, lot_name, lot_type, total_spaces, available_spaces,
                 price_per_hour, price_per_day, currency, distance_to_terminal,
                 has_ev_charging, has_disabled_spaces, is_covered, is_open)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
            """, (airport_iata, f"{lt} Parking",
                  lt, random.randint(200, 3000),
                  random.randint(0, 500),
                  round(random.uniform(5, 50), 1),
                  round(random.uniform(50, 300), 0),
                  "CNY" if airport_iata in ["PEK","PVG","CAN","CTU","SHA","HKG"] else "USD",
                  f"{random.randint(1,15)} min walk",
                  random.randint(0, 1), 1,
                  random.randint(0, 1)))

    # ground transport
    transport_types = [
        ("Express Train", 30, "¥25-35", 10, "06:00-23:00"),
        ("Metro/Subway", 50, "¥4-8", 8, "06:30-23:00"),
        ("Bus", 70, "¥16-30", 20, "06:00-22:00"),
        ("Taxi", 45, "¥80-150", 0, "24h"),
        ("Ride Share", 40, "¥70-120", 0, "24h"),
        ("Shuttle Bus", 60, "¥30-50", 30, "07:00-22:00"),
    ]
    for airport_iata in [a[0] for a in AIRPORTS]:
        for ttype, dur, price, freq, hours in transport_types:
            cursor.execute("""
                INSERT INTO ground_transport
                (airport_iata, transport_type, destination, duration_min, price_range,
                 frequency_min, operating_hours)
                VALUES (?,?,?,?,?,?,?)
            """, (airport_iata, ttype, "City Center", dur, price, freq, hours))

    # weather
    weather_descs = ["Clear", "Partly Cloudy", "Overcast", "Light Rain", "Fog", "Thunderstorm", "Haze"]
    wind_dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    for airport_iata in [a[0] for a in AIRPORTS]:
        cursor.execute("""
            INSERT INTO weather_conditions
            (airport_iata, recorded_at, temperature_c, feels_like_c, humidity_pct,
             wind_speed_kmh, wind_direction, visibility_km, weather_desc, is_vmc,
             ceiling_ft, precipitation_mm)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (airport_iata,
              datetime.now().strftime("%Y-%m-%d %H:%M"),
              round(random.uniform(5, 35), 1),
              round(random.uniform(3, 37), 1),
              random.randint(30, 95),
              round(random.uniform(0, 50), 1),
              random.choice(wind_dirs),
              round(random.uniform(1, 15), 1),
              random.choice(weather_descs),
              random.randint(0, 1),
              random.randint(500, 20000),
              round(random.uniform(0, 5), 1)))

    # flight prices
    cabin_classes = [("Economy", 1.0), ("Premium Economy", 1.8), ("Business", 4.0), ("First", 8.0)]
    fare_bases = ["Y", "M", "Q", "B", "J", "F"]
    for fn, (rid, al, orig, dest, flight_num, dur) in route_id_map.items():
        base_price = dur * random.uniform(2.5, 5.0)
        for cabin, mult in cabin_classes:
            seats_avail = random.randint(0, 50)
            cursor.execute("""
                INSERT INTO flight_prices
                (route_id, cabin_class, fare_basis, price, currency, seats_available,
                 valid_from, valid_to, refundable, changeable)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (rid, cabin, random.choice(fare_bases),
                  round(base_price * mult, 0),
                  "CNY", seats_avail,
                  "2026-01-01", "2026-12-31",
                  random.randint(0, 1), 1))

    # check-in counters
    for airport_iata, term_list in TERMINALS.items():
        for t in term_list:
            tcode = t[0]
            for i in range(1, 11):
                cursor.execute("""
                    INSERT OR IGNORE INTO check_in_counters
                    (airport_iata, terminal_code, counter_number, airline_iata,
                     counter_type, is_open, queue_length, open_time, close_time)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (airport_iata, tcode, str(i),
                      random.choice([a[0] for a in AIRLINES]),
                      random.choice(["Standard", "Self-Service Kiosk", "Premium/Business"]),
                      random.randint(0, 1),
                      random.randint(0, 25),
                      "06:00", "22:00"))

    # security checkpoints
    for airport_iata, term_list in TERMINALS.items():
        for t in term_list:
            tcode = t[0]
            for is_prio in [0, 1]:
                cursor.execute("""
                    INSERT INTO security_checkpoints
                    (airport_iata, terminal_code, checkpoint_name, lane_count,
                     open_lanes, is_priority, avg_wait_min, is_open)
                    VALUES (?,?,?,?,?,?,?,1)
                """, (airport_iata, tcode,
                      f"{'Priority ' if is_prio else ''}Security - Terminal {tcode}",
                      random.randint(4, 10),
                      random.randint(2, 8),
                      is_prio, random.randint(3, 30)))

    conn.commit()
    conn.close()
    print(f"✅ Airport database initialized at: {AIRPORT_DB_PATH}")
    return AIRPORT_DB_PATH


if __name__ == "__main__":
    init_airport_db()
