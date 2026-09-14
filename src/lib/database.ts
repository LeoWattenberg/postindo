import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";

import type { InternationalTariffValues, TariffValues } from "./rates";

export interface Location {
  officeId: string;
  sourceName: string;
  kprkId: string;
  ordinal: number;
  slug: string;
}

export interface RouteRate extends TariffValues {
  sourceRow: number;
  sourcePage: number;
  originId: string;
  destinationId: string;
  counterpart: Location;
}

export interface InternationalRate extends InternationalTariffValues {
  sourceRow: number;
  sourcePage: number;
  sourceName: string;
  countryCode: string;
  slug: string;
}

export interface DatabaseStats {
  locations: number;
  rates: number;
  kprk: number;
  internationalRates: number;
}

const databasePath = resolve(
  process.cwd(),
  process.env.POSTINDO_DB_PATH ?? "data/postindo.sqlite",
);

let database: DatabaseSync | undefined;

function getDatabase(): DatabaseSync {
  if (database) return database;

  if (!existsSync(databasePath)) {
    throw new Error(
      `Basis data tidak ditemukan di ${databasePath}. Jalankan scripts/extract_rates.py atau atur POSTINDO_DB_PATH.`,
    );
  }

  database = new DatabaseSync(databasePath, { readOnly: true });
  return database;
}

const locationSelect = `
  office_id AS officeId,
  source_name AS sourceName,
  kprk_id AS kprkId,
  ordinal,
  slug
`;

const tariffSelect = `
  r.source_row AS sourceRow,
  r.source_page AS sourcePage,
  r.origin_id AS originId,
  r.destination_id AS destinationId,
  r.letter_up_to_100g,
  r.letter_over_100g_to_250g,
  r.letter_over_250g_to_500g,
  r.letter_over_500g_to_1000g,
  r.letter_over_1000g_to_2000g,
  r.postcard,
  r.sekogram,
  r.m_bag_per_kg,
  r.parcel_over_2kg_to_3kg,
  r.parcel_each_additional_kg
`;

interface RawRouteRate extends TariffValues {
  sourceRow: number;
  sourcePage: number;
  originId: string;
  destinationId: string;
  counterpartOfficeId: string;
  counterpartSourceName: string;
  counterpartKprkId: string;
  counterpartOrdinal: number;
  counterpartSlug: string;
}

function mapRoute(row: RawRouteRate): RouteRate {
  return {
    sourceRow: row.sourceRow,
    sourcePage: row.sourcePage,
    originId: row.originId,
    destinationId: row.destinationId,
    letter_up_to_100g: row.letter_up_to_100g,
    letter_over_100g_to_250g: row.letter_over_100g_to_250g,
    letter_over_250g_to_500g: row.letter_over_250g_to_500g,
    letter_over_500g_to_1000g: row.letter_over_500g_to_1000g,
    letter_over_1000g_to_2000g: row.letter_over_1000g_to_2000g,
    postcard: row.postcard,
    sekogram: row.sekogram,
    m_bag_per_kg: row.m_bag_per_kg,
    parcel_over_2kg_to_3kg: row.parcel_over_2kg_to_3kg,
    parcel_each_additional_kg: row.parcel_each_additional_kg,
    counterpart: {
      officeId: row.counterpartOfficeId,
      sourceName: row.counterpartSourceName,
      kprkId: row.counterpartKprkId,
      ordinal: row.counterpartOrdinal,
      slug: row.counterpartSlug,
    },
  };
}

export function getLocations(): Location[] {
  return getDatabase()
    .prepare(`SELECT ${locationSelect} FROM locations ORDER BY ordinal`)
    .all() as unknown as Location[];
}

export function getRatesFrom(originId: string): RouteRate[] {
  const rows = getDatabase()
    .prepare(`
      SELECT
        ${tariffSelect},
        l.office_id AS counterpartOfficeId,
        l.source_name AS counterpartSourceName,
        l.kprk_id AS counterpartKprkId,
        l.ordinal AS counterpartOrdinal,
        l.slug AS counterpartSlug
      FROM rates r
      JOIN locations l ON l.office_id = r.destination_id
      WHERE r.origin_id = ?
      ORDER BY l.ordinal
    `)
    .all(originId) as unknown as RawRouteRate[];

  return rows.map(mapRoute);
}

export function getRatesTo(destinationId: string): RouteRate[] {
  const rows = getDatabase()
    .prepare(`
      SELECT
        ${tariffSelect},
        l.office_id AS counterpartOfficeId,
        l.source_name AS counterpartSourceName,
        l.kprk_id AS counterpartKprkId,
        l.ordinal AS counterpartOrdinal,
        l.slug AS counterpartSlug
      FROM rates r
      JOIN locations l ON l.office_id = r.origin_id
      WHERE r.destination_id = ?
      ORDER BY l.ordinal
    `)
    .all(destinationId) as unknown as RawRouteRate[];

  return rows.map(mapRoute);
}

export function getInternationalRates(): InternationalRate[] {
  return getDatabase()
    .prepare(`
      SELECT
        source_row AS sourceRow,
        source_page AS sourcePage,
        source_name AS sourceName,
        country_code AS countryCode,
        slug,
        letter_printed_matter_small_packet_up_to_20g,
        letter_printed_matter_small_packet_over_20g_to_50g,
        letter_printed_matter_small_packet_over_50g_to_100g,
        letter_printed_matter_small_packet_over_100g_to_250g,
        letter_printed_matter_small_packet_over_250g_to_500g,
        letter_printed_matter_small_packet_over_500g_to_1000g,
        letter_printed_matter_small_packet_over_1000g_to_1500g,
        letter_printed_matter_small_packet_over_1500g_to_2000g,
        postcard,
        sekogram_up_to_7kg,
        m_bag_per_kg_up_to_30kg,
        parcel_up_to_3kg_usd_cents,
        parcel_each_additional_kg_usd_cents
      FROM international_rates
      ORDER BY source_row
    `)
    .all() as unknown as InternationalRate[];
}

export function getDatabaseStats(): DatabaseStats {
  const row = getDatabase()
    .prepare(`
      SELECT
        (SELECT COUNT(*) FROM locations) AS locations,
        (SELECT COUNT(*) FROM rates) AS rates,
        (SELECT COUNT(DISTINCT kprk_id) FROM locations) AS kprk,
        (SELECT COUNT(*) FROM international_rates) AS internationalRates
    `)
    .get() as unknown as DatabaseStats;
  return row;
}

export function getMetadata(): Record<string, string> {
  const rows = getDatabase()
    .prepare("SELECT key, value FROM metadata ORDER BY key")
    .all() as unknown as Array<{ key: string; value: string }>;
  return Object.fromEntries(rows.map(({ key, value }) => [key, value]));
}
