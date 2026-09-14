import { defineConfig } from "@playwright/test";

const [repositoryOwner, repositoryName] = (process.env.GITHUB_REPOSITORY ?? "").split("/");
const isUserOrOrganizationSite = Boolean(
  repositoryOwner &&
  repositoryName?.toLowerCase() === `${repositoryOwner.toLowerCase()}.github.io`,
);
const inferredBase = repositoryName && !isUserOrOrganizationSite
  ? `/${repositoryName}`
  : "/";
const rawBase = process.env.BASE_PATH ?? process.env.PUBLIC_BASE_PATH ?? inferredBase;
const base = rawBase === "/" ? "/" : `/${rawBase.replace(/^\/+|\/+$/g, "")}/`;
const origin = "http://127.0.0.1:4321";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "line",
  use: {
    baseURL: new URL(base, origin).href,
    viewport: { width: 360, height: 780 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4321",
    url: new URL(base, origin).href,
    env: {
      ...process.env,
      // Astro otherwise auto-backgrounds under agentic test runners.
      ASTRO_PREVIEW_BACKGROUND: "0",
    },
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
