// ESLint flat config for the dashboard.
//
// Deliberately self-contained: no `import` of shared configs, so it works
// under `npx eslint` with no package.json and no node_modules in the repo.
// The frontend has no build step and no dependencies, and adding a Node
// toolchain to maintain a lint config would undo that.
//
// The rules are chosen for the failures that actually break this page - a
// typo'd global, a variable that no longer exists, an unreachable branch -
// rather than for style. Formatting is not enforced; arguing about it in a
// one-file frontend is not worth anyone's evening.

export default [
  {
    files: ["src/js/**/*.js"],

    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script", // plain <script>, not a module
      globals: {
        // Browser APIs this page actually uses.
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        FormData: "readonly",
        localStorage: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        requestAnimationFrame: "readonly",
      },
    },

    linterOptions: {
      reportUnusedDisableDirectives: "error",
    },

    rules: {
      // Correctness - these catch real breakage.
      "no-undef": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-redeclare": "error",
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-duplicate-case": "error",
      "no-unreachable": "error",
      "no-constant-condition": "error",
      "no-self-assign": "error",
      "no-sparse-arrays": "error",
      "use-isnan": "error",
      "valid-typeof": "error",
      "no-cond-assign": "error",
      "no-func-assign": "error",
      "no-obj-calls": "error",

      // Async mistakes. The dashboard has already shipped one bug from
      // reading event.currentTarget after an await; these catch neighbours
      // of that class.
      "no-async-promise-executor": "error",
      "require-atomic-updates": "error",

      // Habits that prevent whole categories of bug.
      "no-var": "error",
      "prefer-const": "error",
      eqeqeq: ["error", "smart"],
      "no-eval": "error",
      "no-implied-eval": "error",

      // Off on purpose. app.js is a classic <script> with top-level function
      // declarations, which is the whole point - no build step, no module
      // loader, nothing to install. The rule guards against collisions
      // between multiple scripts on one page, and this page loads exactly
      // one. Worth turning back on (and wrapping the file in an IIFE) the
      // day a second script appears.
      "no-implicit-globals": "off",
    },
  },
];
