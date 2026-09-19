// @ts-check
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // .venv/**: the Python toolchain's own virtualenv, present in CI/local
    // sandboxes once `uv sync` runs (FTF-0101's idpyoidc dependency pulls in
    // urllib3, which ships a vendored .js worker file inside its wheel) --
    // vendored third-party content, not this repo's own TS/JS source, same
    // category as node_modules/dist/.next above.
    ignores: ["**/dist/**", "**/node_modules/**", "**/.next/**", "**/.venv/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts", "**/*.tsx"],
    rules: {
      "@typescript-eslint/no-unused-vars": "error",
    },
  },
);
