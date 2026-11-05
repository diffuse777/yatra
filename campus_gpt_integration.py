"""
campus_gpt_integration.py
─────────────────────────
Python helper that Campus GPT can use to query Yatra Saarthi.

Usage (Python side of Campus GPT):
────────────────────────────────────
    from campus_gpt_integration import YatraSaarthiClient

    ys = YatraSaarthiClient()                          # default: http://localhost:5000

    # Natural-language query (full chatbot flow)
    result = ys.ask("Check timings for Krishnankovil to Madurai")
    print(result['answer'])

    # Direct structured calls
    buses  = ys.get_buses("Krishnankovil", "Madurai")
    trains = ys.get_trains("Krishnankovil", "Bengaluru")
    road   = ys.get_road_info("Krishnankovil", "Bengaluru")
"""

import requests
from typing import Optional


class YatraSaarthiClient:
    """
    Lightweight client that wraps all Yatra Saarthi API calls.
    Use this inside Campus GPT to fetch transport data.
    """

    def __init__(self, base_url: str = "https://yatrasaarthi.vercel.app", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    # ─────────────────────────────────────────────────────────────────────────
    # 1.  NATURAL LANGUAGE QUERY  (main Campus GPT entry point)
    # ─────────────────────────────────────────────────────────────────────────
    def ask(self, query: str) -> dict:
        """
        Send a natural-language query to Yatra Saarthi.
        Returns a dict with keys: intent, origin, destination, answer, data.

        Example:
            result = ys.ask("Check bus timings from Krishnankovil to Madurai")
            bot_reply = result['answer']
        """
        try:
            r = requests.get(
                f"{self.base_url}/api/campus-gpt/query",
                params={"q": query},
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            return {
                "intent": "error",
                "answer": (
                    "Could not connect to Yatra Saarthi. "
                    "Please make sure the Yatra Saarthi server is running on port 5000."
                ),
                "data": {},
            }
        except Exception as e:
            return {"intent": "error", "answer": f"Error: {str(e)}", "data": {}}

    # ─────────────────────────────────────────────────────────────────────────
    # 2.  DIRECT STRUCTURED CALLS
    # ─────────────────────────────────────────────────────────────────────────
    def get_buses(self, origin: str, destination: str) -> dict:
        """
        Get all govt + private buses between two cities.

        Returns:
            {
              'govt_buses':    [ {departure, arrival, operator, fare, seats, bus_type, ...} ],
              'private_buses': [ {departure, arrival, operator_name, fare, rating, ...} ],
              'total_count':   int,
              'answer':        str   (human-readable summary)
            }
        """
        result = self.ask(f"Check bus timings from {origin} to {destination}")
        return {
            "govt_buses":    result.get("data", {}).get("govt_buses", []),
            "private_buses": result.get("data", {}).get("private_buses", []),
            "total_count":   result.get("data", {}).get("total_count", 0),
            "answer":        result.get("answer", ""),
        }

    def get_next_bus(self, origin: str, destination: str) -> Optional[dict]:
        """
        Returns the next available bus (earliest departure) or None.
        """
        buses = self.get_buses(origin, destination)
        all_buses = buses["govt_buses"] + buses["private_buses"]
        if not all_buses:
            return None
        # Sort by departure_time string (HH:MM)
        all_buses.sort(key=lambda b: b.get("departure_time", b.get("departure", "99:99")))
        return all_buses[0]

    def get_trains(self, origin: str, destination: str, date: str = "") -> dict:
        """
        Get live trains between two cities via erail.in API.

        Returns:
            {
              'from_code': str, 'from_name': str,
              'to_code':   str, 'to_name':   str,
              'trains':    [ {number, name, dep_time, arr_time, duration, classes, run_days} ],
              'count':     int,
              'nearest_station_used': bool,
              'nearest_dist_km': float
            }
        """
        try:
            params = {"origin": origin, "destination": destination}
            if date:
                params["date"] = date
            r = requests.get(
                f"{self.base_url}/api/live-trains",
                params=params,
                timeout=60,          # erail.in can be slow
            )
            r.raise_for_status()
            d = r.json()
            return {
                "from_code":             d.get("from_code", ""),
                "from_name":             d.get("from_name", ""),
                "to_code":               d.get("to_code", ""),
                "to_name":               d.get("to_name", ""),
                "trains":                d.get("trains", []),
                "count":                 d.get("count", 0),
                "nearest_station_used":  d.get("from_nearest_used", False),
                "nearest_dist_km":       d.get("from_nearest_dist"),
                "status":                d.get("status", "ok"),
            }
        except Exception as e:
            return {"trains": [], "count": 0, "status": "error", "error": str(e)}

    def get_road_info(self, origin: str, destination: str) -> Optional[dict]:
        """
        Get road distance and travel time estimates.

        Returns:
            {
              'distance_km': int,
              'car_hours': int, 'car_mins': int,
              'bike_hours': int, 'bike_mins': int,
              'car_fuel': int,  'bike_fuel': int
            }
        or None if not available.
        """
        result = self.ask(f"road distance from {origin} to {destination}")
        data = result.get("data", {})
        return data if data.get("distance_km") else None

    def get_nearby_stations(self, city: str, radius_km: int = 100) -> list:
        """
        Get list of nearby railway stations within radius_km.

        Returns list of: {code, name, city, distance_km}
        """
        try:
            r = requests.get(
                f"{self.base_url}/api/nearby-stations",
                params={"city": city, "radius": radius_km},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("stations", [])
        except Exception:
            return []

    def get_nearby_airports(self, city: str, radius_km: int = 80) -> list:
        """
        Get nearby airports within radius_km.

        Returns list of: {code, name, city, distance_km, terminal}
        """
        try:
            r = requests.get(
                f"{self.base_url}/api/nearby-airports",
                params={"city": city, "radius": radius_km},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("airports", [])
        except Exception:
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # 3.  CHATBOT RESPONSE FORMATTER  (ready-to-display text)
    # ─────────────────────────────────────────────────────────────────────────
    def format_bus_reply(self, origin: str, destination: str) -> str:
        """
        Returns a clean, chat-friendly reply about buses between two cities.
        Paste this directly into your Campus GPT bot message.
        """
        buses = self.get_buses(origin, destination)
        if buses["total_count"] == 0:
            return (
                f"Sorry, I couldn't find any buses from **{origin}** to **{destination}**.\n"
                f"You can check more options here: "
                f"http://localhost:5000/passengers/timetables?origin={origin}&destination={destination}"
            )
        return buses["answer"]

    def format_train_reply(self, origin: str, destination: str) -> str:
        """
        Returns a clean chat reply about trains between two cities.
        """
        trains = self.get_trains(origin, destination)
        if trains["count"] == 0:
            note = ""
            if trains.get("nearest_station_used"):
                note = f" (searched from nearest station)"
            return (
                f"No trains found from **{origin}** to **{destination}**{note} today.\n"
                f"Try checking directly on IRCTC: https://www.irctc.co.in/nget/train-search"
            )
        lines = [f"**Trains from {origin} to {destination}** ({trains['count']} found):"]
        if trains.get("nearest_station_used"):
            lines.append(
                f"_(Boarding from nearest station: **{trains['from_code']}** "
                f"— {trains['nearest_dist_km']} km away)_"
            )
        for t in trains["trains"][:5]:
            lines.append(
                f"• {t['number']} {t['name']}  |  "
                f"Dep: {t.get('dep_time','--:--')}  |  "
                f"Arr: {t.get('arr_time','--:--')}  |  "
                f"{t.get('duration','')}"
            )
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Quick test — run: python campus_gpt_integration.py
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ys = YatraSaarthiClient("https://yatrasaarthi.vercel.app")

    print("=" * 60)
    print("TEST 1: Natural language query")
    print("=" * 60)
    result = ys.ask("Check timings for Krishnankovil to Madurai")
    print("Intent   :", result.get("intent"))
    print("Origin   :", result.get("origin"))
    print("Dest     :", result.get("destination"))
    print("Answer:\n", result.get("answer"))

    print("\n" + "=" * 60)
    print("TEST 2: get_buses()")
    print("=" * 60)
    buses = ys.get_buses("Krishnankovil", "Madurai")
    print("Govt buses   :", len(buses["govt_buses"]))
    print("Private buses:", len(buses["private_buses"]))

    print("\n" + "=" * 60)
    print("TEST 3: format_bus_reply()")
    print("=" * 60)
    print(ys.format_bus_reply("Krishnankovil", "Madurai"))
