/** Deterministic FR-locale explanation sentences for routing decisions. */

const SENTENCES: Record<string, string> = {
  heatmap: "Suit la heatmap {heat_sport} ({heat_pct}% des chemins).",
  heatmap_trail: "Sentiers balisés ({trail_types}) + heatmap {heat_sport} ({heat_pct}%).",
  heatmap_trail_official: "Itinéraire officiel {trail_types} — heatmap {heat_sport} ({heat_pct}%).",
  heatmap_detour: "Détour +{detour_pct}% pour suivre la heatmap {heat_sport} ({heat_pct}%).",
  personal: "Construit avec vos traces personnelles.",
  trail: "Suit un sentier balisé.",
  trail_official: "Suit l'itinéraire officiel {trail_types}.",
  direct: "Itinéraire le plus direct ({surface_summary}).",
  no_data: "Pas de données communautaires — tracé de repli.",
  no_graph_path: "Aucun chemin routable trouvé. Ajoutez un point intermédiaire.",
  no_heatmap: "Pas de données heatmap dans cette zone.",
};

export const SURFACE_FR: Record<string, string> = {
  asphalt: "asphalte",
  gravel: "gravel",
  dirt: "terre",
  rock: "rocher",
  unknown: "inconnu",
};

const SPORT_FR: Record<string, string> = {
  road: "route",
  gravel: "gravel",
  mtb: "VTT",
  offroad: "off-road",
  running: "course",
};

export function surfaceSummary(surfacePct: Record<string, number> | undefined): string {
  if (!surfacePct) return "";
  const known = Object.entries(surfacePct)
    .filter(([k, v]) => k !== "unknown" && v > 0)
    .sort((a, b) => b[1] - a[1]);
  if (known.length === 0) return "";
  if (known[0][1] >= 80) return SURFACE_FR[known[0][0]] || known[0][0];
  if (known.length >= 2) {
    const n1 = SURFACE_FR[known[0][0]] || known[0][0];
    const n2 = SURFACE_FR[known[1][0]] || known[1][0];
    return `${Math.round(known[0][1])}% ${n1}, ${Math.round(known[1][1])}% ${n2}`;
  }
  return `${Math.round(known[0][1])}% ${SURFACE_FR[known[0][0]] || known[0][0]}`;
}

function heatSportSummary(heatSportPct: Record<string, number> | undefined): string {
  if (!heatSportPct) return "communauté";
  const sorted = Object.entries(heatSportPct)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  if (sorted.length === 0) return "communauté";
  const top = sorted[0];
  const name = SPORT_FR[top[0]] || top[0];
  if (top[1] >= 80) return name;
  if (sorted.length >= 2) {
    const n2 = SPORT_FR[sorted[1][0]] || sorted[1][0];
    return `${name} + ${n2}`;
  }
  return name;
}

export interface RouteExplanation {
  reason_code: string;
  secondary_reasons: string[];
  sentence_key: string;
  metrics: {
    heat_avg: number;
    heat_used_ratio: number;
    detour_ratio: number;
    trail_used: boolean;
    trail_types: string[];
    method: string;
    surface_pct?: Record<string, number>;
    heat_sport_pct?: Record<string, number>;
  };
}

export function renderSentence(explanation: RouteExplanation): string {
  const template = SENTENCES[explanation.sentence_key] || SENTENCES["direct"];
  const { metrics } = explanation;
  const ss = surfaceSummary(metrics.surface_pct);
  const hs = heatSportSummary(metrics.heat_sport_pct);
  return template
    .replace("{detour_pct}", String(Math.round((metrics.detour_ratio - 1) * 100)))
    .replace("{trail_types}", metrics.trail_types.join(" / ") || "balisé")
    .replace("{heat_pct}", String(Math.round(metrics.heat_used_ratio * 100)))
    .replace("{heat_sport}", hs)
    .replace("{surface_summary}", ss || "mixte");
}

/** Compact badge text for inline display. */
export function badgeText(explanation: RouteExplanation): string {
  const { reason_code, metrics } = explanation;
  if (reason_code === "HEATMAP_PREFERRED") {
    const sport = heatSportSummary(metrics.heat_sport_pct);
    return `🔥 ${sport} ${Math.round(metrics.heat_used_ratio * 100)}%`;
  }
  if (reason_code === "PERSONAL_TRACES") return "📍 mes traces";
  if (reason_code === "TRAIL_PREFERRED") {
    const t = metrics.trail_types[0];
    return t ? `🔰 ${t}` : "🔰 balisé";
  }
  return "";
}

/** Badge color. */
export function badgeColor(explanation: RouteExplanation): string {
  switch (explanation.reason_code) {
    case "HEATMAP_PREFERRED": return "#2d6a4f";
    case "PERSONAL_TRACES": return "#8e44ad";
    case "TRAIL_PREFERRED": return "#388e3c";
    default: return "#888";
  }
}

const METHOD_LABEL_FR: Record<string, string> = {
  community_heatmap: "Heatmap",
  personal_traces: "Mes traces",
  dfci_trails: "DFCI",
  marked_trails: "Balisé",
  straight_line: "Ligne droite",
  no_route: "Pas de chemin",
  fallback: "Repli",
};

const METHOD_ICON: Record<string, string> = {
  community_heatmap: "🔥",
  personal_traces: "📍",
  dfci_trails: "🔰",
  marked_trails: "🥾",
};

/** One-liner summary for a single segment result (used in tooltips & panel). */
export function renderSegmentSummary(method: string, explanation?: RouteExplanation): string {
  const icon = METHOD_ICON[method] ?? "";
  const label = METHOD_LABEL_FR[method] ?? method;
  if (!explanation) return `${icon} ${label}`.trim();
  const { reason_code, metrics } = explanation;
  if (reason_code === "HEATMAP_PREFERRED" && metrics.heat_used_ratio > 0) {
    const sport = heatSportSummary(metrics.heat_sport_pct);
    return `${icon} ${label} ${sport} (${Math.round(metrics.heat_used_ratio * 100)}%)`;
  }
  return `${icon} ${label}`.trim();
}
