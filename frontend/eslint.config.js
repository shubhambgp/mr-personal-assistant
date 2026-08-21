// Flat config (eslint 9). The `lint` script in package.json predated this file
// and failed for want of it; CI now runs it.
//
// The Tailwind rules are the ones that earn their keep:
//
//   no-unknown-classes     `brand-950` survived four references, a passing build
//                          and green CI, because Tailwind emits nothing for a
//                          class it does not recognise and never warns. This
//                          turns that silence into a failure. The plugin reads
//                          the real v4 entry point, so the tokens in
//                          styles/theme.css are what it validates against.
//   no-conflicting-classes two callers override Button's size, and which one
//                          wins is decided by stylesheet order, not by argument
//                          order. tailwind-merge fixes it; this rule finds it.

import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import betterTailwind from 'eslint-plugin-better-tailwindcss'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'better-tailwindcss': betterTailwind,
    },
    settings: {
      'better-tailwindcss': { entryPoint: 'src/styles/index.css' },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // Dead classes and typos become failures rather than silence.
      'better-tailwindcss/no-unknown-classes': [
        'error',
        // Hand-written CSS, not Tailwind: the markdown block, its table scroll
        // wrapper, the scrollbar utility, and the theme-swap class itself.
        { ignore: ['md', 'table-wrap', 'scrollbar-thin', 'dark'] },
      ],
      'better-tailwindcss/no-conflicting-classes': 'error',
      'better-tailwindcss/no-duplicate-classes': 'error',
      'better-tailwindcss/no-deprecated-classes': 'error',

      // `void promise` is the deliberate idiom here for fire-and-forget calls.
      '@typescript-eslint/no-misused-promises': ['error', { checksVoidReturn: false }],
    },
  },
  // Config files are not part of the app's tsconfig project.
  {
    files: ['*.config.{js,ts}'],
    extends: [tseslint.configs.disableTypeChecked],
  },
)
