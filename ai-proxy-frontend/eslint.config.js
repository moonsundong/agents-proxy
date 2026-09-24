// ESLint 9 flat config:Vue 3 + TypeScript(文档 8.2 节)
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  ...pluginVue.configs['flat/essential'],
  {
    // TS 规则只作用于纯 TS 文件;.vue 的 script 由 vue 解析器委托给 TS 解析器
    files: ['**/*.ts', '**/*.mts', '**/*.tsx'],
    extends: [tseslint.configs.recommended],
  },
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    // 路由页面与根组件按惯例使用单词名
    files: ['src/views/**/*.vue', 'src/App.vue'],
    rules: { 'vue/multi-word-component-names': 'off' },
  },
)
