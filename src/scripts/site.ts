const normalize = (value: string): string =>
  value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("id-ID")
    .replace(/[^a-z0-9]/g, "");

for (const root of document.querySelectorAll<HTMLElement>("[data-filter-root]")) {
  const input = root.querySelector<HTMLInputElement>("[data-filter-input]");
  const list = root.querySelector<HTMLElement>("[data-filter-list]");
  const items = list ? [...list.children] as HTMLElement[] : [];
  const searchTerms = items.map((item) => {
    const searchable = item.matches("tr") ? item.querySelector("th") : item;
    return normalize(searchable?.textContent ?? "");
  });
  const count = root.querySelector<HTMLElement>("[data-result-count]");
  const empty = root.querySelector<HTMLElement>("[data-empty-state]");

  if (!input) continue;

  const filter = (): void => {
    const query = normalize(input.value);
    let visible = 0;

    for (const [index, item] of items.entries()) {
      const matches = !query || searchTerms[index].includes(query);
      item.hidden = !matches;
      if (matches) visible += 1;
    }

    if (count) {
      const noun = root.hasAttribute("data-rate-controls") ? "rute" : "wilayah/kantor";
      count.textContent = `${visible.toLocaleString("id-ID")} ${noun}`;
    }
    if (empty) empty.hidden = visible !== 0;
  };

  input.addEventListener("input", filter);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && input.value) {
      input.value = "";
      filter();
    }
  });
}

for (const controls of document.querySelectorAll<HTMLElement>("[data-rate-controls]")) {
  const select = controls.querySelector<HTMLSelectElement>("[data-service-select]");
  const table = controls.querySelector<HTMLElement>("[data-rate-table]");
  if (!select || !table) continue;

  const showService = (): void => {
    table.dataset.column = String(select.selectedIndex + 2);
  };

  showService();
  select.addEventListener("change", showService);
}

const finder = document.querySelector<HTMLFormElement>("[data-route-finder]");
if (finder) {
  finder.addEventListener("submit", (event) => {
    event.preventDefault();
    const origin = finder.querySelector<HTMLSelectElement>("[data-origin-select]");
    const destination = finder.querySelector<HTMLSelectElement>("[data-destination-select]");
    const error = finder.querySelector<HTMLElement>("[data-route-finder-error]");
    const originOption = origin?.selectedOptions.item(0);
    const destinationOption = destination?.selectedOptions.item(0);

    if (!origin?.value || !destination?.value || !originOption || !destinationOption) {
      if (error) error.hidden = false;
      (!origin?.value ? origin : destination)?.focus();
      return;
    }

    if (error) error.hidden = true;
    const submitter = (event as SubmitEvent).submitter as HTMLButtonElement | null;
    const viewFromDestination = submitter?.value === "ke";
    const url = viewFromDestination
      ? `${destination.value}#dari-${encodeURIComponent(originOption.dataset.officeId ?? "")}`
      : `${origin.value}#ke-${encodeURIComponent(destinationOption.dataset.officeId ?? "")}`;
    window.location.assign(url);
  });
}
