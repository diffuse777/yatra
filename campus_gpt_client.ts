/**
 * campus_gpt_client.ts
 * ─────────────────────
 * TypeScript client for Campus GPT to call the Yatra Saarthi API.
 *
 * USAGE in your Campus GPT TypeScript project:
 * ─────────────────────────────────────────────
 *   import { YatraSaarthiClient, formatBotReply } from "./campus_gpt_client";
 *
 *   const ys = new YatraSaarthiClient();
 *
 *   // When user types a message in the chatbot:
 *   const userMessage = "Check bus timings from Krishnankovil to Madurai";
 *   const reply = await ys.ask(userMessage);
 *   displayInChat(reply.answer);
 */

// ─── Types ────────────────────────────────────────────────────────────────────

export interface BusEntry {
  departure_time: string;
  arrival_time:   string;
  operator:       string;
  bus_type:       string;
  fare:           number;
  seat_availability: number;
  status:         string;
  via_stops?:     string[];
}

export interface PrivateBusEntry {
  departure:  string;
  arrival:    string;
  operator:   string;
  bus_type:   string;
  fare:       number;
  rating:     number;
  duration:   string;
  route:      string;
}

export interface TrainEntry {
  number:    string;
  name:      string;
  dep_time:  string;
  arr_time:  string;
  duration:  string;
  classes:   string[];
  run_days:  string[];
}

export interface QueryResponse {
  intent:       string;          // "bus_timings" | "train_timings" | "road_distance" | "fare_info" | etc.
  mode:         string;          // "bus" | "train" | "flight" | "road"
  origin:       string;
  destination:  string;
  query:        string;
  answer:       string;          // ← paste this directly as the bot reply
  data:         Record<string, unknown>;
  yatraSaarthiUrl: string;       // deep link to Yatra Saarthi search results
}

export interface RoadInfo {
  distance_km: number;
  car_hours:   number;
  car_mins:    number;
  bike_hours:  number;
  bike_mins:   number;
  car_fuel:    number;
  bike_fuel:   number;
}

// ─── Client class ─────────────────────────────────────────────────────────────

export class YatraSaarthiClient {
  private baseUrl: string;

  constructor(baseUrl = "https://yatrasaarthi.vercel.app") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // ── 1. NATURAL LANGUAGE QUERY  (main Campus GPT entry point) ──────────────
  /**
   * Send any natural-language message and get a structured response.
   * The `answer` field is ready to display directly in the chatbot UI.
   *
   * @example
   *   const res = await ys.ask("Check bus timings from Krishnankovil to Madurai");
   *   chatBubble.text = res.answer;
   */
  async ask(query: string): Promise<QueryResponse> {
    try {
      const url = `${this.baseUrl}/api/campus-gpt/query?q=${encodeURIComponent(query)}`;
      const res  = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json() as QueryResponse;
    } catch (err) {
      return {
        intent: "error",
        mode: "unknown",
        origin: "",
        destination: "",
        query,
        answer: "Sorry, I could not connect to Yatra Saarthi right now. Please try again.",
        data: {},
        yatraSaarthiUrl: this.baseUrl,
      };
    }
  }

  // ── 2. GET BUSES (structured) ──────────────────────────────────────────────
  async getBuses(origin: string, destination: string) {
    const res = await this.ask(`bus timings from ${origin} to ${destination}`);
    const d   = res.data as Record<string, unknown>;
    return {
      govtBuses:    (d.govt_buses    as BusEntry[])        ?? [],
      privateBuses: (d.private_buses as PrivateBusEntry[]) ?? [],
      totalCount:   (d.total_count   as number)            ?? 0,
      answer:       res.answer,
      deepLink:     res.yatraSaarthiUrl,
    };
  }

  // ── 3. GET TRAINS (live from erail.in) ────────────────────────────────────
  async getTrains(origin: string, destination: string, date = "") {
    try {
      let url = `${this.baseUrl}/api/live-trains?origin=${encodeURIComponent(origin)}&destination=${encodeURIComponent(destination)}`;
      if (date) url += `&date=${date}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      return {
        fromCode:           d.from_code  as string,
        fromName:           d.from_name  as string,
        toCode:             d.to_code    as string,
        toName:             d.to_name    as string,
        trains:             d.trains     as TrainEntry[],
        count:              d.count      as number,
        nearestStationUsed: d.from_nearest_used as boolean,
        nearestDistKm:      d.from_nearest_dist as number | null,
        status:             d.status     as string,
      };
    } catch {
      return { trains: [], count: 0, status: "error" };
    }
  }

  // ── 4. GET ROAD INFO ──────────────────────────────────────────────────────
  async getRoadInfo(origin: string, destination: string): Promise<RoadInfo | null> {
    const res = await this.ask(`road distance from ${origin} to ${destination}`);
    const d   = res.data as Partial<RoadInfo>;
    return d.distance_km ? (d as RoadInfo) : null;
  }

  // ── 5. GET NEARBY STATIONS ────────────────────────────────────────────────
  async getNearbyStations(city: string, radiusKm = 100) {
    try {
      const url = `${this.baseUrl}/api/nearby-stations?city=${encodeURIComponent(city)}&radius=${radiusKm}`;
      const res = await fetch(url);
      if (!res.ok) return [];
      const d = await res.json();
      return d.stations as { code: string; name: string; city: string; distance_km: number }[];
    } catch {
      return [];
    }
  }
}

// ─── Standalone helper — use this directly in your chatbot handler ────────────

const _client = new YatraSaarthiClient();

/**
 * Drop-in function for your Campus GPT message handler.
 *
 * Call this whenever you detect a transport-related query.
 * Returns a ready-to-display bot reply string.
 *
 * @example
 *   // In your chatbot message handler:
 *   if (isTransportQuery(userMessage)) {
 *     const reply = await getYatraReply(userMessage);
 *     sendBotMessage(reply);
 *   }
 */
export async function getYatraReply(userMessage: string): Promise<string> {
  const result = await _client.ask(userMessage);
  const link   = `\n\n🔗 Full details: ${result.yatraSaarthiUrl}`;
  return result.answer + (result.intent !== "error" ? link : "");
}

/**
 * Detect if a user message is transport-related.
 * Use this to decide whether to call Yatra Saarthi or your normal GPT flow.
 *
 * @example
 *   if (isTransportQuery(msg)) {
 *     reply = await getYatraReply(msg);
 *   } else {
 *     reply = await callCampusGPT(msg);
 *   }
 */
export function isTransportQuery(message: string): boolean {
  const keywords = [
    "bus", "train", "flight", "travels", "route",
    "timing", "timings", "schedule", "depart", "arrive",
    "fare", "ticket", "seat", "available", "next bus",
    "when is", "how to reach", "distance", "road",
    "tnstc", "setc", "kpn", "srs", "vrl", "irctc",
    "station", "airport", "go to", "travel to",
    "from krishnan", "from srivilliputtur", "from madurai",
    "to bengaluru", "to chennai", "to madurai",
  ];
  const lower = message.toLowerCase();
  return keywords.some((kw) => lower.includes(kw));
}

// ─── Example: React / Next.js chatbot integration ────────────────────────────
/*
import { isTransportQuery, getYatraReply } from "./campus_gpt_client";

async function handleUserMessage(userText: string): Promise<string> {
  if (isTransportQuery(userText)) {
    // 👇 This calls Yatra Saarthi and returns formatted bus/train info
    return await getYatraReply(userText);
  }
  // 👇 Otherwise call your normal Campus GPT / OpenAI logic
  return await callYourGPT(userText);
}
*/
