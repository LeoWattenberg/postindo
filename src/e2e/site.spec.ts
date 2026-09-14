import { expect, test } from "@playwright/test";

test("menemukan rute, mengganti layanan, dan membuka indeks sebaliknya", async ({ page }) => {
  await page.goto("./");

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
  await context.close();
});
