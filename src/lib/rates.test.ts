import assert from "node:assert/strict";
import test from "node:test";

import { formatInternationalTariff, formatTariff, normalizeSearch } from "./rates.ts";

test("pencarian mengabaikan kapitalisasi, spasi, dan tanda baca", () => {
  assert.equal(normalizeSearch("KAB. Aceh Barat-Daya"), "kabacehbaratdaya");
  assert.equal(normalizeSearch("  Kec. A/B  "), "kecab");
});

test("tarif menggunakan format rupiah Indonesia tanpa desimal", () => {
  assert.equal(formatTariff("letter_up_to_100g", 12500), "Rp12.500");
});

test("sekogram nol ditampilkan sebagai bebas biaya", () => {
  assert.equal(formatTariff("sekogram", 0), "Bebas Biaya");
});

test("tarif internasional membedakan rupiah, sen dolar AS, dan nilai kosong", () => {
  assert.equal(
    formatInternationalTariff("letter_printed_matter_small_packet_up_to_20g", 17500),
    "Rp17.500",
  );
  assert.equal(formatInternationalTariff("parcel_up_to_3kg_usd_cents", 1234), "US$12,34");
  assert.equal(formatInternationalTariff("sekogram_up_to_7kg", 0), "Bebas Biaya");
  assert.equal(formatInternationalTariff("m_bag_per_kg_up_to_30kg", null), "Tidak tersedia");
});
