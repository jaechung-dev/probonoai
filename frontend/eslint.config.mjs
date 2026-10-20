import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// Vite + React 19 + TypeScript. Replaces the eslint-config-next presets, which
// were left behind by the Next.js -> Vite migration and no longer installed.
export default tseslint.config(
  { ignores: ["dist/**", "out/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Best-effort calls (conversation saves, optional fetches) use `catch {}` on purpose.
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
);
