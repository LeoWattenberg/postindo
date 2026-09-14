const configuredBase = import.meta.env.BASE_URL === "/"
  ? ""
  : import.meta.env.BASE_URL.replace(/\/$/, "");

export function sitePath(path = "/"): string {
  const normalizedPath = `/${path.replace(/^\/+/, "")}`;
  return `${configuredBase}${normalizedPath}`;
}
