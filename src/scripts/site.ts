const normalize = (value: string): string =>
  value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("id-ID")
    .replace(/[^a-z0-9]/g, "");

const finder = document.querySelector<HTMLFormElement>("[data-route-finder]");
const originSelect = finder?.querySelector<HTMLSelectElement>("[data-origin-select]");
const destinationSelect = finder?.querySelector<HTMLSelectElement>("[data-destination-select]");

// Send the office list once, then populate the destination before enhancing either picker.
if (originSelect && destinationSelect) {
  const options = document.createDocumentFragment();
  for (const option of originSelect.options) {
    if (!option.value) continue;
    const destinationOption = option.cloneNode(true) as HTMLOptionElement;
    destinationOption.value = option.value.replace(/\/dari\/([^/]+)\/$/, "/ke/$1/");
    options.append(destinationOption);
  }
  destinationSelect.append(options);
}

for (const root of document.querySelectorAll<HTMLElement>("[data-searchable-select]")) {
  const select = root.querySelector<HTMLSelectElement>("select");
  const label = root.querySelector<HTMLLabelElement>("label");
  if (!select || !label) continue;

  const input = document.createElement("input");
  input.id = `${select.id}-search`;
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.required = true;
  input.placeholder = select.options[0]?.value ? "Cari jenis kiriman" : select.options[0].text;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", `${select.id}-options`);

  const panel = document.createElement("div");
  panel.className = "select-panel";
  panel.hidden = true;
  const list = document.createElement("ul");
  list.id = `${select.id}-options`;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", label.textContent?.trim() ?? "");
  const empty = document.createElement("p");
  empty.className = "select-empty";
  empty.textContent = "Tidak ada pilihan yang cocok.";
  empty.setAttribute("role", "status");
  empty.hidden = true;

  const options = [...select.options].filter((option) => option.value);
  const items = options.map((option, index) => {
    const item = document.createElement("li");
    item.id = `${select.id}-option-${index}`;
    item.setAttribute("role", "option");
    item.textContent = option.text;
    list.append(item);
    return item;
  });
  const searchTerms = options.map((option) => normalize(option.text));
  let activeIndex = -1;
  let committedValue = select.value;

  const sync = (): void => {
    input.value = select.value ? select.selectedOptions[0]?.text ?? "" : "";
    input.setCustomValidity("");
    for (const [index, item] of items.entries()) {
      item.setAttribute("aria-selected", String(options[index].value === select.value));
    }
  };

  const activate = (index: number): void => {
    activeIndex = index;
    for (const [itemIndex, item] of items.entries()) {
      item.classList.toggle("is-active", itemIndex === index);
    }
    if (index < 0) {
      input.removeAttribute("aria-activedescendant");
    } else {
      input.setAttribute("aria-activedescendant", items[index].id);
      items[index].scrollIntoView({ block: "nearest" });
    }
  };

  const open = (query = ""): void => {
    const term = normalize(query);
    for (const [index, item] of items.entries()) {
      item.hidden = Boolean(term) && !searchTerms[index].includes(term);
    }
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    empty.hidden = items.some((item) => !item.hidden);
    activate(-1);
  };

  const close = (): void => {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
    activate(-1);
    select.value = committedValue;
    sync();
  };

  const choose = (index: number): void => {
    select.value = options[index].value;
    committedValue = select.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    close();
  };

  input.addEventListener("focus", () => {
    open();
    input.select();
  });
  input.addEventListener("click", () => {
    if (panel.hidden) {
      open();
      input.select();
    }
  });
  input.addEventListener("input", () => {
    select.selectedIndex = -1;
    input.setCustomValidity("Pilih salah satu pilihan dalam daftar.");
    open(input.value);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (panel.hidden) open();
      const visible = items.flatMap((item, index) => item.hidden ? [] : [index]);
      const position = visible.indexOf(activeIndex);
      const next = position < 0
        ? (event.key === "ArrowDown" ? 0 : visible.length - 1)
        : Math.max(0, Math.min(visible.length - 1, position + (event.key === "ArrowDown" ? 1 : -1)));
      activate(visible[next] ?? -1);
    } else if (event.key === "Enter" && !panel.hidden) {
      event.preventDefault();
      if (activeIndex >= 0) choose(activeIndex);
    } else if ((event.key === "Home" || event.key === "End") && !panel.hidden && activeIndex >= 0) {
      event.preventDefault();
      const visible = items.flatMap((item, index) => item.hidden ? [] : [index]);
      activate((event.key === "Home" ? visible[0] : visible.at(-1)) ?? -1);
    }
  });
  // Keep focus on the combobox while choosing with a mouse or touch.
  panel.addEventListener("pointerdown", (event) => event.preventDefault());
  for (const [index, item] of items.entries()) {
    item.addEventListener("click", () => choose(index));
  }
  input.addEventListener("blur", close);
  select.addEventListener("change", () => {
    committedValue = select.value;
    sync();
  });
  select.form?.addEventListener("reset", () => {
    // The browser restores the select's default after the reset event.
    queueMicrotask(() => {
      committedValue = select.value;
      close();
    });
  });

  panel.append(list, empty);
  root.append(input, panel);
  label.htmlFor = input.id;
  select.required = false;
  select.hidden = true;
  sync();
}

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
      const noun = root.dataset.resultNoun
        ?? (root.hasAttribute("data-rate-controls") ? "rute" : "wilayah/kantor");
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

  const requestedService = new URLSearchParams(window.location.search).get("layanan");
  if (
    requestedService &&
    [...select.options].some((option) => option.value === requestedService)
  ) {
    select.value = requestedService;
  }

  const showService = (): void => {
    table.dataset.column = String(select.selectedIndex + 2);
  };

  showService();
  select.addEventListener("change", showService);
}

if (finder) {
  finder.addEventListener("submit", (event) => {
    event.preventDefault();
    const origin = finder.querySelector<HTMLSelectElement>("[data-origin-select]");
    const destination = finder.querySelector<HTMLSelectElement>("[data-destination-select]");
    const service = finder.querySelector<HTMLSelectElement>("[data-route-service-select]");
    const error = finder.querySelector<HTMLElement>("[data-route-finder-error]");
    const originOption = origin?.selectedOptions.item(0);
    const destinationOption = destination?.selectedOptions.item(0);

    if (!origin?.value || !destination?.value || !originOption || !destinationOption) {
      if (error) error.hidden = false;
      const missing = !origin?.value ? origin : destination;
      missing?.closest("[data-searchable-select]")?.querySelector("input")?.focus();
      return;
    }

    if (error) error.hidden = true;
    const url = new URL(origin.value, window.location.href);
    if (service?.value) url.searchParams.set("layanan", service.value);
    url.hash = `ke-${encodeURIComponent(destinationOption.dataset.officeId ?? "")}`;
    window.location.assign(url.href);
  });
}
