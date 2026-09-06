import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: {
    // `jsdom` only where a test asks for it. The pure formatter and lot-matching
    // suites are the bulk of the tests and run an order of magnitude faster in
    // node, so the DOM is opted into per file with a
    // `@vitest-environment jsdom` docblock rather than imposed on everything.
    environment: "node",
    setupFiles: ["./vitest.setup.ts"],
    // Vitest shares one jsdom document across the tests in a file. `cleanup` in
    // the setup file unmounts between them; `restoreMocks` undoes the fetch stubs
    // component tests install, so a leaked stub cannot make the next file pass.
    restoreMocks: true,
    unstubGlobals: true,
  },
});
