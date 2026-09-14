import assert from "node:assert/strict";
import test from "node:test";

import { formatTariff, normalizeSearch } from "./rates.ts";

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
