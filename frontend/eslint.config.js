import globals from "globals";
import pluginVue from "eslint-plugin-vue";
import vueTsEslintConfig from "@vue/eslint-config-typescript";

export default [
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**"],
  },
  ...pluginVue.configs["flat/recommended"],
  ...vueTsEslintConfig(),
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      "vue/multi-word-component-names": "off",
      "vue/html-self-closing": [
        "error",
        {
          html: { void: "always", normal: "always", component: "always" },
          svg: "always",
          math: "always",
        },
      ],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["tests/**/*.{ts,js,vue}"],
    rules: {
      "vue/one-component-per-file": "off",
      "vue/require-default-prop": "off",
    },
  },
];
