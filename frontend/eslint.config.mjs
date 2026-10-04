import eslint from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

const eslintConfig = tseslint.config(
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
);

export default eslintConfig;
