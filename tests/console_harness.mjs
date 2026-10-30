// Shared loader for the seller console tests: the console is three classic scripts that share one
// global scope (i18n.js, console-render.js, console.js), so they run in order in one vm context.
import { readFileSync } from "node:fs";
import vm from "node:vm";

export const STATIC = new URL("../app/static/", import.meta.url);
export const CONSOLE_FILES = ["i18n.js", "console-render.js", "console.js"];
export const read = (name) => readFileSync(new URL(name, STATIC), "utf8");

export function loadConsole(context) {
  for (const name of CONSOLE_FILES) vm.runInContext(read(name), context, { filename: name });
  return (code) => vm.runInContext(code, context);
}
