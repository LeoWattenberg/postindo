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

export function formatTariff(key: ServiceKey, value: number): string {
  if (key === "sekogram" && value === 0) return "Bebas Biaya";
  return rupiahFormatter.format(value).replace(/\u00a0/g, "");
}
