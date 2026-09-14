import { expect, test } from "@playwright/test";

test("menemukan rute, mengganti layanan, dan membuka indeks sebaliknya", async ({ page }) => {
  await page.goto("./");

  await expect(page).toHaveTitle("ongkirstempel");
  await expect(page.locator(".brand")).toContainText("ongkirstempel");
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute(
    "href",
    /\/postindo\/favicon\.svg$/,
  );
  await expect(
    page.locator('footer a[href="https://kw.media/en/website-design-management/"]'),
  ).toHaveText("Desain dan pengelolaan oleh kw.media");
  await expect(page.locator('footer a[href="https://kw.media/impressum/"]')).toHaveText(
    "Impressum",
  );

  const originOptions = page.locator("[data-origin-select] option");
  const destinationOptions = page.locator("[data-destination-select] option");
  expect(await originOptions.count()).toBeGreaterThan(2);
  expect(await destinationOptions.count()).toBeGreaterThan(2);

  const originId = await originOptions.nth(1).getAttribute("data-office-id");
  const destinationId = await destinationOptions.nth(2).getAttribute("data-office-id");
  expect(originId).toBeTruthy();
  expect(destinationId).toBeTruthy();

  await page.locator("[data-origin-select]").selectOption({ index: 1 });
  await page.locator("[data-destination-select]").selectOption({ index: 2 });
  await page.getByRole("button", { name: "Lihat dari asal" }).click();

  await expect(page).toHaveURL(new RegExp(`#ke-${destinationId}$`));
  const route = page.locator(`[id="ke-${destinationId}"]`);
  await expect(route).toBeVisible();

  const defaultCell = route.locator("td").nth(0);
  await expect(defaultCell).toBeVisible();
  await expect(defaultCell).toHaveText(/^Rp[\d.]+$/);
  const mirroredValue = await defaultCell.innerText();

  const serviceSelect = page.locator("[data-service-select]");
  const serviceCount = await serviceSelect.locator("option").count();
  expect(serviceCount).toBe(10);
  for (let serviceIndex = 0; serviceIndex < serviceCount; serviceIndex += 1) {
    await serviceSelect.selectOption({ index: serviceIndex });
    const enhancedTableSize = await page.locator(".table-scroll").evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(enhancedTableSize.scrollWidth).toBeLessThanOrEqual(enhancedTableSize.clientWidth + 1);
  }

  await serviceSelect.selectOption("postcard");
  await expect(route.locator("td").nth(5)).toBeVisible();
  await expect(defaultCell).toBeHidden();

  const counterpartName = (await route.locator("th > a").first().innerText()).trim();
  const punctuationQuery = counterpartName.toLocaleLowerCase("id-ID").split("").join(". ");
  await page.locator("[data-filter-input]").fill(punctuationQuery);
  await expect(route).toBeVisible();
  await expect(page.locator("tbody tr:visible")).toHaveCount(1);
  await expect(page.locator("[data-result-count]")).toHaveText("1 rute");
  await page.locator("[data-filter-input]").press("Escape");
  await expect(page.locator("[data-filter-input]")).toHaveValue("");
  await expect(page.locator("tbody tr:visible")).toHaveCount(await page.locator("tbody tr").count());

  await route.locator("th > a").first().click();
  await expect(page).toHaveURL(new RegExp(`#dari-${originId}$`));
  const mirroredRoute = page.locator(`[id="dari-${originId}"]`);
  await expect(mirroredRoute).toBeVisible();
  await expect(mirroredRoute.locator("td").nth(0)).toHaveText(mirroredValue);

  const bodyOverflows = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(bodyOverflows).toBe(false);
});

test("mencari tujuan dan membaca mata uang tarif internasional", async ({ page }) => {
  await page.goto("internasional/");

  await expect(page.getByRole("heading", { name: "Tarif pos internasional" })).toBeVisible();
  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(236);

  const serviceSelect = page.locator("[data-service-select]");
  const serviceCount = await serviceSelect.locator("option").count();
  expect(serviceCount).toBe(13);
  const firstRow = rows.first();
  await expect(firstRow.locator("td").first()).toHaveText(/^(Rp[\d.]+|Tidak tersedia)$/);

  for (let serviceIndex = 0; serviceIndex < serviceCount; serviceIndex += 1) {
    await serviceSelect.selectOption({ index: serviceIndex });
    await expect(firstRow.locator("td").nth(serviceIndex)).toBeVisible();
    const enhancedTableSize = await page.locator(".table-scroll").evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(enhancedTableSize.scrollWidth).toBeLessThanOrEqual(
      enhancedTableSize.clientWidth + 1,
    );
  }

  await serviceSelect.selectOption("sekogram_up_to_7kg");
  await expect(firstRow.locator("td").nth(9)).toHaveText("Bebas Biaya");
  await serviceSelect.selectOption("m_bag_per_kg_up_to_30kg");
  await expect(page.locator("#tristan-da-cunha-ta td").nth(10)).toHaveText(
    "Tidak tersedia",
  );

  await serviceSelect.selectOption("parcel_up_to_3kg_usd_cents");
  await expect(firstRow.locator("td").nth(11)).toBeVisible();
  await expect(firstRow.locator("td").first()).toBeHidden();
  await expect(firstRow.locator("td").nth(11)).toHaveText(/^(US\$[\d.,]+|Tidak tersedia)$/);

  const destinationName = (await firstRow.locator("th strong").innerText()).trim();
  const normalizedQuery = destinationName.toLocaleLowerCase("id-ID").split("").join("- ");
  await page.locator("[data-filter-input]").fill(normalizedQuery);
  await expect(firstRow).toBeVisible();
  await expect(page.locator("tbody tr:visible")).toHaveCount(1);
  await expect(page.locator("[data-result-count]")).toHaveText("1 negara/tujuan");
  await page.locator("[data-filter-input]").press("Escape");
  await expect(page.locator("[data-filter-input]")).toHaveValue("");
  await expect(page.locator("tbody tr:visible")).toHaveCount(236);

  const tableSize = await page.locator(".table-scroll").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(tableSize.scrollWidth).toBeLessThanOrEqual(tableSize.clientWidth + 1);
});

test("tanpa JavaScript tetap menampilkan kesepuluh kolom tarif", async ({ browser, baseURL }) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    viewport: { width: 360, height: 780 },
  });
  const page = await context.newPage();
  await page.goto(baseURL ?? "/");
  await expect(page.locator(".route-finder")).toBeHidden();

  const firstOriginUrl = await page
    .locator("[data-origin-select] option")
    .nth(1)
    .getAttribute("value");
  expect(firstOriginUrl).toBeTruthy();
  await page.goto(firstOriginUrl ?? "");

  await expect(page.locator("noscript .notice")).toBeVisible();
  await expect(page.locator(".rate-tools")).toBeHidden();
  const serviceHeaders = page.locator("thead th[data-service]");
  await expect(serviceHeaders).toHaveCount(10);
  for (const header of await serviceHeaders.all()) await expect(header).toBeVisible();
  const firstRouteCells = page.locator("tbody tr").first().locator("td");
  await expect(firstRouteCells).toHaveCount(10);
  for (const cell of await firstRouteCells.all()) await expect(cell).toBeVisible();

  const fallbackTableSize = await page.locator(".table-scroll").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(fallbackTableSize.scrollWidth).toBeGreaterThan(fallbackTableSize.clientWidth);

  const bodyOverflows = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(bodyOverflows).toBe(false);

  await page.goto(new URL("internasional/", baseURL).href);
  await expect(page.locator("noscript .notice")).toBeVisible();
  const internationalHeaders = page.locator("thead th[data-service]");
  await expect(internationalHeaders).toHaveCount(13);
  for (const header of await internationalHeaders.all()) await expect(header).toBeVisible();
  const internationalCells = page.locator("tbody tr").first().locator("td");
  await expect(internationalCells).toHaveCount(13);
  for (const cell of await internationalCells.all()) await expect(cell).toBeVisible();
  const internationalFallbackSize = await page.locator(".table-scroll").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(internationalFallbackSize.scrollWidth).toBeGreaterThan(
    internationalFallbackSize.clientWidth,
  );
  const internationalBodyOverflows = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(internationalBodyOverflows).toBe(false);
  await context.close();
});
