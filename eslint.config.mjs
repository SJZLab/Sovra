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
  {
    // `.cjs`: a real Node.js CommonJS script by construction (the
    // extension itself fixes `sourceType`/module system, e.g. FTF-04A10's
    // own `_typescript_javascript_ast_helper.cjs`, a subprocess helper
    // invoked from Python -- never part of `apps/web`'s browser/ESM build).
    files: ["**/*.cjs"],
    languageOptions: {
      sourceType: "commonjs",
      globals: {
        require: "readonly",
        module: "readonly",
        exports: "readonly",
        process: "readonly",
        Buffer: "readonly",
        __dirname: "readonly",
        __filename: "readonly",
        console: "readonly",
      },
    },
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
);
