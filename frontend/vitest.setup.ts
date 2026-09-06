/** Test environment for component tests.
 *
 *  Two things, both of which are easy to forget and painful to debug:
 *
 *  `@testing-library/jest-dom` adds the DOM matchers -- `toBeInTheDocument`,
 *  `toHaveTextContent` -- that make a failure message say what was on screen
 *  rather than that two objects differ.
 *
 *  `cleanup` unmounts between tests. Vitest does not isolate the jsdom document
 *  per test, so without it a second render finds two copies of everything and
 *  `getByText` throws "found multiple elements" on a test that is actually fine.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
