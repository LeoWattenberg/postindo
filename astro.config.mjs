import { defineConfig } from "astro/config";

const repositoryParts = (process.env.GITHUB_REPOSITORY ?? "").split("/");
const repositoryOwner = repositoryParts.at(0);
const repositoryName = repositoryParts.at(1);
const isUserOrOrganizationSite = Boolean(
  repositoryOwner &&
  repositoryName?.toLowerCase() === `${repositoryOwner.toLowerCase()}.github.io`,
);

function normalizeBase(value) {
  if (!value || value === "/") return "/";
  return `/${value.replace(/^\/+|\/+$/g, "")}`;
}

const inferredBase = repositoryName && !isUserOrOrganizationSite ? `/${repositoryName}` : "/";
const base = normalizeBase(
  process.env.BASE_PATH ?? process.env.PUBLIC_BASE_PATH ?? inferredBase,
);
const site =
  process.env.SITE_URL ??
  (repositoryOwner ? `https://${repositoryOwner}.github.io` : "http://localhost:4321");

export default defineConfig({
  site,
  base,
  output: "static",
  trailingSlash: "always",
  build: {
    format: "directory",
  },
  vite: {
    build: {
      // Keep the one shared client module external instead of duplicating it in every page.
      assetsInlineLimit: 0,
    },
  },
});
