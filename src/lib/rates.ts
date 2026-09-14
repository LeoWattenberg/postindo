export const SERVICE_DEFINITIONS = [
  {
    key: "letter_up_to_100g",
    label: "Surat ≤100 g",
    description: "Surat sampai dengan 100 gram",
  },
  {
    key: "letter_over_100g_to_250g",
    label: "Surat >100–250 g",
    description: "Surat di atas 100 sampai dengan 250 gram",
  },
  {
    key: "letter_over_250g_to_500g",
    label: "Surat >250–500 g",
    description: "Surat di atas 250 sampai dengan 500 gram",
  },
  {
    key: "letter_over_500g_to_1000g",
    label: "Surat >500–1.000 g",
    description: "Surat di atas 500 sampai dengan 1.000 gram",
  },
  {
    key: "letter_over_1000g_to_2000g",
    label: "Surat >1.000–2.000 g",
    description: "Surat di atas 1.000 sampai dengan 2.000 gram",
  },
  {
    key: "postcard",
    label: "Kartupos",
    description: "Kartupos",
  },
  {
    key: "sekogram",
    label: "Sekogram",
    description: "Sekogram (bebas biaya)",
  },
  {
    key: "m_bag_per_kg",
    label: "M-Bag per kg",
    description: "M-Bag per kilogram",
  },
  {
    key: "parcel_over_2kg_to_3kg",
    label: "Paket >2–3 kg",
    description: "Paket di atas 2 sampai dengan 3 kilogram",
  },
  {
    key: "parcel_each_additional_kg",
    label: "Paket per kg berikutnya",
    description: "Paket untuk setiap kilogram berikutnya",
  },
] as const;

export type ServiceKey = (typeof SERVICE_DEFINITIONS)[number]["key"];

export type TariffValues = Record<ServiceKey, number>;

export const INTERNATIONAL_SERVICE_DEFINITIONS = [
  {
    key: "letter_printed_matter_small_packet_up_to_20g",
    label: "Kiriman ≤20 g",
    description: "Surat, barang cetakan, dan bungkusan kecil sampai dengan 20 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_20g_to_50g",
    label: "Kiriman >20–50 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 20 sampai dengan 50 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_50g_to_100g",
    label: "Kiriman >50–100 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 50 sampai dengan 100 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_100g_to_250g",
    label: "Kiriman >100–250 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 100 sampai dengan 250 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_250g_to_500g",
    label: "Kiriman >250–500 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 250 sampai dengan 500 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_500g_to_1000g",
    label: "Kiriman >500–1.000 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 500 sampai dengan 1.000 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_1000g_to_1500g",
    label: "Kiriman >1.000–1.500 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 1.000 sampai dengan 1.500 gram",
    currency: "IDR",
  },
  {
    key: "letter_printed_matter_small_packet_over_1500g_to_2000g",
    label: "Kiriman >1.500–2.000 g",
    description: "Surat, barang cetakan, dan bungkusan kecil di atas 1.500 sampai dengan 2.000 gram",
    currency: "IDR",
  },
  {
    key: "postcard",
    label: "Kartupos",
    description: "Kartupos",
    currency: "IDR",
  },
  {
    key: "sekogram_up_to_7kg",
    label: "Sekogram ≤7 kg",
    description: "Sekogram sampai dengan 7 kilogram (bebas biaya)",
    currency: "IDR",
  },
  {
    key: "m_bag_per_kg_up_to_30kg",
    label: "M-Bag per kg ≤30 kg",
    description: "M-Bag per kilogram sampai dengan 30 kilogram",
    currency: "IDR",
  },
  {
    key: "parcel_up_to_3kg_usd_cents",
    label: "Paket ≤3 kg (USD)",
    description: "Paket sampai dengan 3 kilogram dalam dolar AS",
    currency: "USD",
  },
  {
    key: "parcel_each_additional_kg_usd_cents",
    label: "Paket per kg berikutnya (USD)",
    description: "Paket untuk setiap kilogram berikutnya dalam dolar AS",
    currency: "USD",
  },
] as const;

export type InternationalServiceKey =
  (typeof INTERNATIONAL_SERVICE_DEFINITIONS)[number]["key"];

export type InternationalTariffValues = Record<InternationalServiceKey, number | null>;

export function normalizeSearch(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("id-ID")
    .replace(/[^a-z0-9]/g, "");
}

const rupiahFormatter = new Intl.NumberFormat("id-ID", {
  style: "currency",
  currency: "IDR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

const usdNumberFormatter = new Intl.NumberFormat("id-ID", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatRupiah(value: number): string {
  return rupiahFormatter.format(value).replace(/\u00a0/g, "");
}

export function formatTariff(key: ServiceKey, value: number): string {
  if (key === "sekogram" && value === 0) return "Bebas Biaya";
  return formatRupiah(value);
}

export function formatInternationalTariff(
  key: InternationalServiceKey,
  value: number | null,
): string {
  if (value === null) return "Tidak tersedia";
  if (key === "sekogram_up_to_7kg" && value === 0) return "Bebas Biaya";
  if (key.endsWith("_usd_cents")) return `US$${usdNumberFormatter.format(value / 100)}`;
  return formatRupiah(value);
}
